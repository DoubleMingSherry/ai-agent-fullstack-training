# -*- coding: utf-8 -*-
"""Mini Adapter + SSE Encoder（practice/3_adapter.md）

- Adapter: 接收 chunk 迭代器（仅依赖 choices[0].delta.content / finish_reason / usage 属性），
  归一化为内部事件（Harness 事件，唯一代码表示为 dict，含 type 与 seq）。
- Encoder: 将内部事件编码为 SSE bytes；与 Adapter 互相独立、无共享状态。
- 全程不发起任何网络请求：HTTP 调用不混进 Adapter，网络边界由调用方负责。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Iterator

EVENT_TYPES = frozenset({"text.delta", "model.finished", "model.usage"})


# ---------------------------------------------------------------------------
# Adapter：chunk 迭代器 -> 内部事件（dict）
# ---------------------------------------------------------------------------
class MiniAdapter:
    """把上游 chunk 迭代器归一化为内部事件流。

    只读取属性，不持有连接：想测就得发请求 ⇒ 设计有误，这里保证不会。
    """

    def __init__(self) -> None:
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def adapt(self, chunks: Iterable[Any]) -> list[dict]:
        """便利入口：一次性归一化整个迭代器。"""
        return list(self.adapt_iter(chunks))

    def adapt_iter(self, chunks: Iterable[Any]) -> Iterator[dict]:
        for chunk in chunks:
            yield from self.adapt_chunk(chunk)

    def adapt_chunk(self, chunk: Any) -> Iterator[dict]:
        """归一化单个 chunk。

        - role-only（空 delta）与非法空 chunk：不产生事件，seq 不递增
        - 非空文本 delta：text.delta，内容逐字保留
        - 空字符串 delta：不产生事件，不消耗 seq
        - finish_reason：model.finished
        - 非空 usage：model.usage
        """
        choices = getattr(chunk, "choices", None)
        choice = choices[0] if choices else None

        delta = getattr(choice, "delta", None) if choice is not None else None
        text = getattr(delta, "content", None) if delta is not None else None

        if text:  # None / "" 均不产生事件
            yield {"type": "text.delta", "seq": self._next_seq(), "text": text}

        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason:
            yield {
                "type": "model.finished",
                "seq": self._next_seq(),
                "finish_reason": finish_reason,
            }

        usage = getattr(chunk, "usage", None)
        if usage is not None:
            usage_event = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            }
            if any(usage_event.values()):  # usage 非空才发事件
                yield {"type": "model.usage", "seq": self._next_seq(), "usage": usage_event}


# ---------------------------------------------------------------------------
# Encoder：内部事件（dict）-> SSE bytes
# ---------------------------------------------------------------------------
def encode_sse(event: dict) -> bytes:
    """内部事件 -> SSE 帧字节：id/event/data 三行 + 空行结尾。

    安全性来自 json.dumps：
    - payload 中的 \n、\r\n、引号、反斜杠都会被转义，data 恒为单行，
      因此不会破坏 "三行 + 空行" 的帧结构；
    - ensure_ascii=False 保证中文以 UTF-8 原样保留，往返不丢字。
    """
    data = json.dumps(event, ensure_ascii=False)
    frame = f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"
    return frame.encode("utf-8")


def encode_heartbeat() -> bytes:
    """心跳帧：以 ": " 开头的注释行，SSE 解析器不视为事件。"""
    return b": heartbeat\n\n"


def parse_sse(frame: bytes) -> dict | None:
    """最小 SSE 解析器（仅用于测试往返）：返回事件 dict，注释帧返回 None。"""
    id_: str | None = None
    event_type: str | None = None
    data_lines: list[str] = []
    for line in frame.decode("utf-8").split("\n"):
        if line.startswith(":"):  # 注释行（心跳）不构成事件
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "id":
            id_ = value
        elif field == "event":
            event_type = value
        elif field == "data":
            data_lines.append(value)

    if event_type is None and not data_lines:
        return None
    event = json.loads("\n".join(data_lines))
    assert str(event["seq"]) == id_
    assert event["type"] == event_type
    return event


# ---------------------------------------------------------------------------
# 伪造 chunk（不需要真正调用大模型，也不需要 openai）
# ---------------------------------------------------------------------------
class FakeDelta:
    def __init__(self, content: str | None = None, role: str | None = None) -> None:
        self.content = content
        self.role = role


class FakeChoice:
    def __init__(self, delta: FakeDelta | None = None, finish_reason: str | None = None) -> None:
        self.delta = delta if delta is not None else FakeDelta()
        self.finish_reason = finish_reason


class FakeUsage:
    
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeChunk:
    def __init__(self, choices: list[FakeChoice] | None = None, usage: FakeUsage | None = None) -> None:
        self.choices = choices if choices is not None else []
        self.usage = usage


def make_role_only_chunk() -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(delta=FakeDelta(role="assistant"))])


def make_text_chunk(text: str) -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(delta=FakeDelta(content=text))])


def make_finish_chunk(finish_reason: str = "stop") -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(finish_reason=finish_reason)])


def make_usage_chunk() -> FakeChunk:
    return FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=12, completion_tokens=34, total_tokens=46))


def make_empty_chunk() -> FakeChunk:
    return FakeChunk()  # 非法空 chunk


# ---------------------------------------------------------------------------
# 测试（纯断言/pytest，无网络）
# ---------------------------------------------------------------------------
def test_five_chunk_kinds() -> None:
    adapter = MiniAdapter()
    chunks = [
        make_role_only_chunk(),   # 1. role-only 空 delta：无事件
        make_text_chunk("你好"),  # 2. 带 delta 文本：text.delta
        make_finish_chunk(),      # 3. finish_reason="stop"：model.finished
        make_usage_chunk(),       # 4. usage 非空：model.usage
        make_empty_chunk(),       # 5. 非法空 chunk：无事件
    ]
    events = adapter.adapt(chunks)

    assert [e["type"] for e in events] == ["text.delta", "model.finished", "model.usage"]
    assert all(e["type"] in EVENT_TYPES for e in events)
    assert [e["seq"] for e in events] == [1, 2, 3]  # role-only / 空 chunk 不消耗 seq
    assert events[0]["text"] == "你好"
    assert events[1]["finish_reason"] == "stop"
    assert events[2]["usage"] == {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}


def test_role_only_and_empty_chunk_produce_no_events() -> None:
    adapter = MiniAdapter()
    assert adapter.adapt([make_role_only_chunk(), make_empty_chunk()]) == []
    assert adapter.seq == 0


def test_text_delta_preserves_content_char_by_char() -> None:
    text = "你好，\"世界\"！\\n换行\t制表 emoji: 😀"
    adapter = MiniAdapter()
    events = adapter.adapt(make_text_chunk(t) for t in text)  # 逐字喂入
    assert [e["type"] for e in events] == ["text.delta"] * len(text)
    assert "".join(e["text"] for e in events) == text  # 逐字保留


def test_empty_delta_does_not_consume_seq() -> None:
    adapter = MiniAdapter()
    events = adapter.adapt(
        [
            make_text_chunk("前"),
            make_text_chunk(""),  # 空字符串 delta
            make_text_chunk("后"),
        ]
    )
    assert [e["seq"] for e in events] == [1, 2]
    assert "".join(e["text"] for e in events) == "前后"
    assert adapter.seq == 2  # 空 delta 不漂移 seq


def test_sse_frame_format_and_valid_json() -> None:
    event = {"type": "text.delta", "seq": 7, "text": "你好"}
    frame = encode_sse(event)

    assert isinstance(frame, bytes)
    lines = frame.decode("utf-8").split("\n")
    assert lines[-1] == "" and lines[-2] == ""  # 以空行结尾
    body = lines[:-2]
    assert len(body) == 3  # id/event/data 三行
    assert body[0] == "id: 7"
    assert body[1] == "event: text.delta"
    assert body[2].startswith("data: ")
    assert json.loads(body[2][len("data: "):]) == event  # data 是合法 JSON


def test_unicode_roundtrip() -> None:
    event = {"type": "text.delta", "seq": 1, "text": "中文·往返不丢字 ✅ «引用» 『引号\'双引号\""}
    assert parse_sse(encode_sse(event))["text"] == event["text"]


def test_newline_and_quotes_do_not_break_frame() -> None:
    for text in ("第一行\n第二行", "第一行\r\n第二行\r纯\r", '引号 " 反斜杠 \\\' 混合'):
        event = {"type": "text.delta", "seq": 1, "text": text}
        frame = encode_sse(event)
        body = frame.decode("utf-8").rstrip("\n").split("\n")
        assert len(body) == 3  # 帧结构未被换行符破坏
        assert body[2].startswith("data: ")  # data 恒为单行
        assert parse_sse(frame)["text"] == text  # \n、\r\n、引号、反斜杠正确转义往返


def test_large_payload_over_64kb() -> None:
    big_text = "大数据块" * (70 * 1024 // len("大数据块".encode("utf-8")))  # > 64 KB
    assert len(big_text.encode("utf-8")) > 64 * 1024
    event = {"type": "text.delta", "seq": 1, "text": big_text}

    frame = encode_sse(event)  # 单事件整体编码，不切帧
    body = frame.decode("utf-8").rstrip("\n").split("\n")
    assert len(body) == 3
    assert len(body[2].encode("utf-8")) > 64 * 1024  # data 行字节数超过 64 KB
    assert parse_sse(frame)["text"] == big_text  # 大 payload 往返不丢字


def test_heartbeat_is_comment_not_event() -> None:
    frame = encode_heartbeat()
    assert frame.startswith(b": ")  # 注释行开头
    assert parse_sse(frame) is None  # 解析器确认不构成事件


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台防乱码
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(tests)} 个测试通过，无任何网络请求。")
