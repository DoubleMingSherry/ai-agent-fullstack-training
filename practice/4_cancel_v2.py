# -*- coding: utf-8 -*-
"""协作式取消 v2（practice/4_cancel.md；在 4_cancel.py 基础上做 seq 所有权收尾）

相对 4_cancel.py 只改一处：把组合根里对 adapter._next_seq 私有方法的直接访问，
提升为公开的 SeqAllocator——seq 空间的唯一所有者。组合根创建并注入它，
事件流仍只依赖一个 next_seq 可调用；跨越 adapter 私有边界的权限收敛到
SeqAllocator 一处。其余零件、行为与测试口径完全一致；全程不 import openai、不发网络请求。

- ChunkFeeder:            模拟上游 chunk 流（可随时继续喂 chunk，不实际调用模型）
- RunStateMachine:        run 生命周期 running -> cancelling -> cancelled，非法跳转 raise
- CancelSignal:           可控取消信号（DI 注入 cancel_check），第 m 次检查返回 True
- CancellableEventStream: 包装 3_adapter 的事件迭代器，每消费 N 个事件调用一次
                          cancel_check()（DI，与 chunk 迭代器同款思路）；触发取消后
                          产出且仅产出一个 run.cancelled 事件（seq = last_seq + 1），
                          之后无论再喂多少 chunk 均零事件
- SeqAllocator:           seq 空间唯一所有者（新增）：adapter 事件 seq 与 run.cancelled
                          的 seq 同源、连续、不撞号，消耗是真的消耗
"""

from __future__ import annotations

import importlib
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# ---------------------------------------------------------------------------
# 复用 3_adapter 的全部零件（文件名以数字开头，只能动态导入）
# ---------------------------------------------------------------------------
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_3adapter = importlib.import_module("3_adapter")

MiniAdapter = _3adapter.MiniAdapter
EVENT_TYPES = _3adapter.EVENT_TYPES
encode_sse = _3adapter.encode_sse
parse_sse = _3adapter.parse_sse
make_role_only_chunk = _3adapter.make_role_only_chunk
make_text_chunk = _3adapter.make_text_chunk
make_finish_chunk = _3adapter.make_finish_chunk
make_usage_chunk = _3adapter.make_usage_chunk
make_empty_chunk = _3adapter.make_empty_chunk

CANCEL_EVENT_TYPE = "run.cancelled"
CANCEL_REASON = "用户取消"  # 原因不由 cancel_check 返回，由包装器写入固定字符串

RUNNING, CANCELLING, CANCELLED = "running", "cancelling", "cancelled"


# ---------------------------------------------------------------------------
# 状态机：running -> cancelling -> cancelled，非法跳转必须 raise
# ---------------------------------------------------------------------------
class IllegalStateTransition(RuntimeError):
    """非法状态跳转（如 running -> cancelled）：必须 raise，不得静默。"""


class RunStateMachine:
    """run 生命周期状态机。

    只允许 running -> cancelling -> cancelled 这一条链。特别地，
    running -> cancelled 被禁止：取消必须先经过 cancelling（在 cancelling
    里保存取消数据），不得一步跳到 cancelled。cancelled 是终态。
    """

    ALLOWED = frozenset({(RUNNING, CANCELLING), (CANCELLING, CANCELLED)})

    def __init__(self) -> None:
        self.state: str = RUNNING

    def transition(self, target: str) -> None:
        if (self.state, target) not in self.ALLOWED:
            raise IllegalStateTransition(f"非法状态跳转: {self.state} -> {target}")
        self.state = target


# ---------------------------------------------------------------------------
# 可控取消信号 + 可继续喂 chunk 的模拟上游
# ---------------------------------------------------------------------------
class CancelSignal:
    """可控的取消信号源（DI 注入 cancel_check）：第 m 次检查返回 True。

    模拟用户操作信号；原因不由这里返回，由包装器写入固定字符串。
    信号闩锁：一旦在第 m 次触发，之后的检查保持 True。
    """

    def __init__(self, m: int) -> None:
        if m < 1:
            raise ValueError("m 必须 >= 1")
        self.m = m
        self.count = 0  # 已被调用次数（= 检查次数）
        self.results: list[bool] = []

    def __call__(self) -> bool:
        self.count += 1
        result = self.count >= self.m
        self.results.append(result)
        return result


class ChunkFeeder:
    """模拟上游 chunk 流（推模型）：feed() 随时继续喂，迭代器端按需拉取。

    用户取消后上游不会立刻停推（服务端不知道用户按了取消），因此取消后
    继续喂是合法操作——由下游保证不再产出任何事件。
    取尽且未 close() 说明拉取方违反协作约定，显式 raise 而非静默提前结束。
    """

    def __init__(self) -> None:
        self._chunks: deque[Any] = deque()
        self._closed = False

    def feed(self, *chunks: Any) -> None:
        self._chunks.extend(chunks)

    def close(self) -> None:
        self._closed = True

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        if self._chunks:
            return self._chunks.popleft()
        if self._closed:
            raise StopIteration
        raise RuntimeError("chunk 流已取尽但上游未 close()：拉取方违反协作约定")


# ---------------------------------------------------------------------------
# 取消检查点：包装 3_adapter 的事件迭代器（DI，和 chunk 迭代器同款思路）
# ---------------------------------------------------------------------------
class CancellableEventStream:
    """每消费 every 个事件调用一次注入的 cancel_check()。

    - 返回 False：继续产出后续事件
    - 返回 True（协作式取消，依次执行）：
        1. 状态机 running -> cancelling：当前检查点之后不再产出任何 model.* 事件
        2. 保存（last_seq、已输出文本、原因）到内存；last_seq 为取消前
           最后一个已产出事件的 seq
        3. 保存完成后，状态机 cancelling -> cancelled
        4. 产出且仅产出一个 run.cancelled 事件（含 last_seq / 已输出文本 / 原因，
           seq = last_seq + 1，经注入的 next_seq 消耗同一 seq 空间）
      之后 __next__ 直接 StopIteration：继续喂 chunk 也产出零事件。
    """

    def __init__(
        self,
        events: Iterable[dict],
        *,
        cancel_check: Callable[[], bool],
        fsm: RunStateMachine,
        next_seq: Callable[[], int],
        every: int = 3,
        reason: str = CANCEL_REASON,
    ) -> None:
        if every < 1:
            raise ValueError("every 必须 >= 1")
        self._events = iter(events)
        self._cancel_check = cancel_check
        self._fsm = fsm
        self._next_seq = next_seq
        self._every = every
        self._reason = reason
        self._since_check = 0
        self.memory: dict | None = None  # 取消数据保存处（内存）
        self.last_seq = 0                # 最后一个已产出事件的 seq
        self.output_text = ""            # 已输出文本（累计 text.delta）

    def __iter__(self) -> Iterator[dict]:
        return self

    def __next__(self) -> dict:
        if self._fsm.state == CANCELLED:
            # 协作式生效：取消后继续喂 chunk 也零事件（含不重复产出 run.cancelled）
            raise StopIteration
        if self._since_check >= self._every:
            self._since_check = 0
            if self._cancel_check():  # 检查点：每消费 every 个事件一次
                return self._cancel()
        event = next(self._events)  # 上游正常结束则自然 StopIteration
        self.last_seq = event["seq"]
        if event["type"] == "text.delta":
            self.output_text += event["text"]
        self._since_check += 1
        return event

    def _cancel(self) -> dict:
        """取消流程：进入 cancelling -> 保存内存 -> 进入 cancelled -> 唯一的 run.cancelled。"""
        self._fsm.transition(CANCELLING)
        self.memory = {
            "last_seq": self.last_seq,
            "output_text": self.output_text,
            "reason": self._reason,
        }
        self._fsm.transition(CANCELLED)  # 保存完成后才进入 cancelled
        return {
            "type": CANCEL_EVENT_TYPE,
            "seq": self._next_seq(),
            "last_seq": self.memory["last_seq"],
            "output_text": self.memory["output_text"],
            "reason": self.memory["reason"],
        }


# ---------------------------------------------------------------------------
# SeqAllocator：seq 空间的唯一所有者（对 adapter 私有边界的唯一授权封装）
# ---------------------------------------------------------------------------
class SeqAllocator:
    """公开的 seq 分配器：adapter 事件与 harness 事件共用同一个递增 seq 空间。

    3_adapter.py 一个字不动，MiniAdapter 只把 seq 分配暴露为私有 _next_seq()；
    跨越这条私有边界的权限被收敛到本类唯一一处。组合根只创建并注入这个
    公开接口，事件流拿到的是不透明的 next_seq 可调用——不知道、也不必知道
    seq 真实出自 adapter 的私有计数器。
    """

    def __init__(self, adapter: MiniAdapter) -> None:
        self._adapter = adapter

    def next_seq(self) -> int:
        """分配下一个 seq：与 adapter 的事件 seq 同源、连续、不撞号。"""
        return self._adapter._next_seq()


# ---------------------------------------------------------------------------
# 组合根：喂 chunk -> MiniAdapter 归一化 -> 取消检查点（全 DI 接线）
# ---------------------------------------------------------------------------
@dataclass
class Run:
    """一次可取消 run 的接线结果（测试用的小型组合根）。"""

    feeder: ChunkFeeder
    adapter: MiniAdapter
    fsm: RunStateMachine
    signal: CancelSignal
    seqs: SeqAllocator
    stream: CancellableEventStream

    def feed(self, *chunks: Any) -> None:
        """继续喂 chunk（取消后也允许：上游不会因用户取消立刻停推）。"""
        self.feeder.feed(*chunks)


def make_run(*, m: int, every: int = 3) -> Run:
    """组装一条可取消 run；cancel_check 与 seq 分配均按 DI 注入。

    seq 所有权在这里说清：SeqAllocator 由组合根创建并注入其公开的 next_seq，
    组合根自身也不再触碰 adapter 的私有方法。
    """
    feeder = ChunkFeeder()
    adapter = MiniAdapter()
    fsm = RunStateMachine()
    signal = CancelSignal(m)
    seqs = SeqAllocator(adapter)
    stream = CancellableEventStream(
        adapter.adapt_iter(feeder),
        cancel_check=signal,
        fsm=fsm,
        next_seq=seqs.next_seq,  # 公开方法：私有访问已收敛进 SeqAllocator
        every=every,
    )
    return Run(feeder=feeder, adapter=adapter, fsm=fsm, signal=signal, seqs=seqs, stream=stream)


# ---------------------------------------------------------------------------
# 测试（纯断言，无网络）
# ---------------------------------------------------------------------------
def _must_raise(exc_type: type[BaseException], fn: Callable[[], Any]) -> None:
    """断言 fn() 抛出 exc_type：非法跳转必须 raise，不得静默。"""
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(f"应 raise {exc_type.__name__}，却静默通过")


def test_state_machine_legal_path() -> None:
    fsm = RunStateMachine()
    assert fsm.state == RUNNING
    fsm.transition(CANCELLING)
    assert fsm.state == CANCELLING
    fsm.transition(CANCELLED)
    assert fsm.state == CANCELLED


def test_illegal_transitions_must_raise() -> None:
    fsm = RunStateMachine()
    _must_raise(IllegalStateTransition, lambda: fsm.transition(CANCELLED))  # running -> cancelled（题目点名的非法跳转）
    _must_raise(IllegalStateTransition, lambda: fsm.transition(RUNNING))    # running -> running 自环
    assert fsm.state == RUNNING  # 非法跳转不得改变状态

    fsm.transition(CANCELLING)
    _must_raise(IllegalStateTransition, lambda: fsm.transition(RUNNING))
    _must_raise(IllegalStateTransition, lambda: fsm.transition(CANCELLING))
    fsm.transition(CANCELLED)  # cancelling -> cancelled：唯一合法出口

    for bad in (RUNNING, CANCELLING, CANCELLED):  # cancelled 是终态，不可再跳
        _must_raise(IllegalStateTransition, lambda bad=bad: fsm.transition(bad))
    assert fsm.state == CANCELLED


def test_cancel_signal_mth_check_true() -> None:
    signal = CancelSignal(m=2)
    assert signal() is False   # 第 1 次检查：未取消
    assert signal() is True    # 第 2 次检查：返回 True（模拟用户取消）
    assert signal() is True    # 信号闩锁：一旦触发保持 True
    assert signal.results == [False, True, True]
    assert signal.count == 3


def test_checkpoint_every_three_events() -> None:
    # 每消费 3 个事件一次检查：6 个事件恰好 2 次检查，检查点落在 e3 / e6 之后
    run = make_run(m=99)  # m=99：永不触发取消，只观察检查节奏
    run.feed(*(make_text_chunk(f"t{i}") for i in range(1, 7)))
    run.feeder.close()
    events = list(run.stream)
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert run.signal.count == 2
    assert run.signal.results == [False, False]
    assert run.fsm.state == RUNNING  # 检查未触发取消，状态机不动


def test_collaborative_cancel_flow() -> None:
    run = make_run(m=2, every=3)  # 每消费 3 个事件检查一次，第 2 次检查触发取消

    # 足够长的流（30 个 chunk），保证至少触发 2 次检查
    pieces = [f"段{i}" for i in range(1, 31)]
    run.feed(*(make_text_chunk(p) for p in pieces))

    events = list(run.stream)

    # 检查点之前：全部是 model.* 事件，seq 连续
    assert [e["type"] for e in events[:6]] == ["text.delta"] * 6
    assert all(e["type"] in EVENT_TYPES for e in events[:6])
    assert [e["seq"] for e in events[:6]] == [1, 2, 3, 4, 5, 6]

    # 产出且仅产出一个 run.cancelled（含 last_seq、已输出文本、原因）
    assert len(events) == 7
    cancelled = events[6]
    assert cancelled["type"] == CANCEL_EVENT_TYPE
    assert cancelled["type"] not in EVENT_TYPES
    assert cancelled["seq"] == 7                        # last_seq + 1
    assert cancelled["last_seq"] == 6                   # 取消前最后一个已产出事件
    assert cancelled["output_text"] == "".join(pieces[:6])
    assert cancelled["reason"] == CANCEL_REASON

    # 状态机已走到 cancelled；取消数据已保存到内存
    assert run.fsm.state == CANCELLED
    assert run.stream.memory == {
        "last_seq": 6,
        "output_text": "".join(pieces[:6]),
        "reason": CANCEL_REASON,
    }
    assert run.signal.results == [False, True]  # 第 1 次检查 False，第 2 次检查 True
    assert run.signal.count == 2

    # 取消后继续喂 chunk：产出零事件（run.cancelled 不得重复产出）
    run.feed(*(make_text_chunk(f"迟到的{i}") for i in range(1, 6)), make_finish_chunk(), make_usage_chunk())
    assert list(run.stream) == []
    assert run.signal.count == 2        # 不再产生新的检查
    assert run.adapter.seq == 7         # seq 停在 run.cancelled，不再消耗
    assert run.fsm.state == CANCELLED
    assert run.stream.memory["last_seq"] == 6  # 保存的数据不变


def test_no_cancel_flows_to_completion() -> None:
    run = make_run(m=99)
    run.feed(
        make_role_only_chunk(),  # 无事件、不消耗 seq
        make_text_chunk("你"),
        make_empty_chunk(),      # 无事件、不消耗 seq
        make_text_chunk("好"),
        make_finish_chunk(),
        make_usage_chunk(),
    )
    run.feeder.close()

    events = list(run.stream)
    assert [e["type"] for e in events] == ["text.delta", "text.delta", "model.finished", "model.usage"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]  # role-only / 空 chunk 不消耗 seq
    assert run.fsm.state == RUNNING       # 从未进入 cancelling
    assert run.stream.memory is None      # 没有取消数据
    assert run.signal.results == [False]  # 4 个事件 / every=3 -> 1 次检查


def test_run_cancelled_survives_sse_roundtrip() -> None:
    # 复用 3_adapter 的 Encoder 零件：run.cancelled 走同一条 SSE 通道，往返不丢字
    cancelled = {
        "type": CANCEL_EVENT_TYPE,
        "seq": 7,
        "last_seq": 6,
        "output_text": "段1段2段3段4段5段6",
        "reason": CANCEL_REASON,
    }
    assert parse_sse(encode_sse(cancelled)) == cancelled


def test_seq_allocator_owns_shared_seq_space() -> None:
    adapter = MiniAdapter()
    seqs = SeqAllocator(adapter)
    events = adapter.adapt([make_text_chunk("a"), make_text_chunk("b"), make_text_chunk("c")])
    assert [e["seq"] for e in events] == [1, 2, 3]  # adapter 事件消耗 1..3
    assert seqs.next_seq() == 4                     # 分配器接着发 4：同源连续不撞号
    assert adapter.seq == 4                         # 消耗是真的消耗：adapter 计数器同步推进
    assert seqs.next_seq() == 5
    assert adapter.seq == 5


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(tests)} 个测试通过，无任何网络请求。")
