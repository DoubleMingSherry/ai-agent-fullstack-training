"""第一步：最小检索闭环。

输入：corpus 里的 Markdown 文档、Python 源码、历史记录。
动作：按标题分块 → 调 MiniMax 生成向量 → 写进 Milvus → 检索 →
      装配上下文 → 让模型带引用回答 → 校验引用与一致性。
结果：标准输出里给出每一步的真实数字，以及两个问题的真实回答。

"""

from __future__ import annotations

import functools
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import assemble, build_prompt, label, verify
from rag.config import COLLECTION
from rag.ingest import build_index, chunk, get_embedding, load_markdown_and_code
from rag.minimax import CALL_STATS, ask, build_llm

QUESTIONS = [
    ("q_easy", "登录会话的有效期是多少分钟？"),
    ("q_hard", "如果我要给 login_demo 增加退出登录功能，需要改哪些文件？验收标准是什么？"),
]
TOP_K = 3
SCORE_THRESHOLD = 0.30


# ── 装饰器层：计时 / 调试信息 / 标题行。主逻辑里不再出现 time.time() ──────────


def timed(fn: Callable[..., Any]) -> Callable[..., Any]:
    """计时。被装饰函数返回 dict 时把毫秒写进 result["ms"]，否则只打印。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        ms = (time.perf_counter() - t0) * 1000
        print(f"[计时] {fn.__name__}  {ms:.0f}ms")
        if isinstance(result, dict):
            result["ms"] = round(ms, 1)
        return result

    return wrapper


def traced(fn: Callable[..., Any]) -> Callable[..., Any]:
    """调试信息：入参摘要 + 返回值摘要。链路改坏时一眼能定位到是哪一步。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        params = ", ".join(
            [_brief(a) for a in args] + [f"{k}={_brief(v)}" for k, v in kwargs.items()]
        )
        print(f"[调试] → {fn.__name__}({params})")
        result = fn(*args, **kwargs)
        print(f"[调试] ← {fn.__name__} = {_brief(result)}")
        return result

    return wrapper


def banner(title: str = "{0}") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """执行前打印标题分隔行，把格式化的 print 从主逻辑里挪走。

    title 支持 str.format，可用被装饰函数的实参占位，例如 "[{2}] {3}"。
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            print(f"\n{'=' * 78}\n{title.format(*args, **kwargs)}\n{'=' * 78}")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _brief(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


# ── 主逻辑层：每个函数只描述一个步骤，计时与日志交给装饰器 ────────────────


@timed
def load_documents() -> dict:
    docs = load_markdown_and_code()
    print(f"[解析] 读到 {len(docs)} 个文档（Markdown/代码/历史记录）")
    return {"docs": docs}


def build_nodes(docs: list) -> list:
    nodes = chunk(docs, strategy="markdown", chunk_size=384, overlap=0)
    print(f"[分块] 策略=按标题  得到 {len(nodes)} 个节点")
    for n in nodes[:6]:
        print(f"       {n.metadata['file_path']:<38} § {n.metadata.get('heading') or '(正文)'}")
    if len(nodes) > 6:
        print(f"       … 其余 {len(nodes) - 6} 个")
    return nodes


@timed
def build_vector_index(nodes: list) -> dict:
    """入库。维度探测 + embedding 计费统计都在这里读走。"""
    index = build_index(nodes, collection=COLLECTION, overwrite=True, embed_model=get_embedding())
    dim = len(get_embedding()._get_query_embedding("维度探测"))
    print(f"[入库] collection={COLLECTION}  dim={dim}")
    print(f"       embedding {CALL_STATS['embed_calls']} 次请求 / {CALL_STATS['embed_texts']} 条文本 "
          f"/ {CALL_STATS['embed_seconds']:.1f}s")
    return {"index": index}


@timed
def retrieve(retriever, question: str) -> dict:
    hits = retriever.retrieve(question)
    print(f"[检索] top_k={TOP_K}")
    for i, h in enumerate(hits, 1):
        print(f"       [{i}] score={h.score:.4f}  {label(h)}")
    return {"hits": hits}


def filter_evidence(hits: list) -> list:
    kept = [h for h in hits if h.score >= SCORE_THRESHOLD]
    print(f"[筛选] 阈值 score>={SCORE_THRESHOLD}：保留 {len(kept)} 条，丢弃 {len(hits) - len(kept)} 条")
    return kept


@timed
def ask_with_context(llm, question: str, context: str) -> dict:
    answer, usage = ask(llm, build_prompt(question, context))
    print(f"[回答]\n{answer}")
    print(f"[用量] prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} "
          f"耗时={usage.get('seconds')}s")
    return {"answer": answer, "usage": usage}


def check_answer(answer: str, kept: list) -> dict:
    v = verify(answer, kept)
    print(f"[引用] 编号={v['citations']}  越界={v['bad_citations'] or '无'}  判定={v['verdict']}")
    for c, src in v["cited_sources"].items():
        print(f"       [{c}] → {src}")
    print(f"[一致性] 有依据 {len(v['supported'])} 个 {[e for _, e in v['supported']]}")
    print(f"         无依据 {len(v['unsupported'])} 个 {[e for _, e in v['unsupported']]}")
    return v


@banner("[{2}] {3}")
def run_one(llm, retriever, qid: str, question: str) -> dict:
    """单个问题走完整条链路：检索 → 筛选 → 装配 → 回答 → 校验。"""
    hits = retrieve(retriever, question)["hits"]
    kept = filter_evidence(hits)
    context = assemble(kept)
    print(f"[装配] 上下文 {len(context)} 字符")

    got = ask_with_context(llm, question, context)
    v = check_answer(got["answer"], kept)

    return {
        "qid": qid,
        "question": question,
        "retrieved": [label(h) for h in hits],
        "answer": got["answer"],
        "usage": got["usage"],
        "verify": {k: v[k] for k in ("citations", "bad_citations", "citation_ok", "unsupported")},
    }


@timed
def main() -> None:
    docs = load_documents()["docs"]
    nodes = build_nodes(docs)
    index = build_vector_index(nodes)["index"]

    retriever = index.as_retriever(similarity_top_k=TOP_K)
    llm = build_llm()
    results = [run_one(llm, retriever, qid, q) for qid, q in QUESTIONS]

    print(f"\n{'=' * 78}\n[汇总] 最小闭环跑通，但结果分两类")
    for r in results:
        print(f"  {r['qid']}: 引用合格={r['verify']['citation_ok']} "
              f"无依据实体={len(r['verify']['unsupported'])} 个 → {r['verify']['unsupported']}")


if __name__ == "__main__":
    t_start = time.perf_counter()
    main()
    print(f"[总耗时] {time.perf_counter() - t_start:.1f}s")
