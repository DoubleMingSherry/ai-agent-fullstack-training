"""第四步：检索后处理。

输入：8 个问题 + 上一步选出的检索配置（BGE 70% + BM25 30%，候选 20）。
动作：① 先看候选集里到底有没有 gold（回答“Rerank 能不能补回没召回的证据”）；
      ② LLM Rerank 重排候选，取前 3；
      ③ 上下文压缩：只留与问题相关的句子；
      ④ 带 Citation 回答 + 一致性校验；
      ⑤ 三种处理方式逐题对比。
结果：标准输出给出每种做法的答案正确率、引用合格率、无依据实体数和延迟。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import assemble, build_prompt, label, verify
from rag.evaluate import (fact_score, hit_at_k, load_questions, matches,
                          recall_at_k)
from rag.ingest import build_index, chunk, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import ask, build_llm
from rag.retrieve import BM25Retriever, rrf_fuse

CAND = 20
TOP_K = 3
WEIGHTS = [0.7, 0.3]

RERANK_PROMPT = """你在给检索结果重排。下面是用户问题和 {n} 条候选证据。

按“这条证据能直接回答用户问题”的程度从高到低排序。
只输出一个 JSON 数组，元素是候选编号（从 1 开始），不要解释，不要代码块。
如果某条证据与问题无关，也放进数组末尾。

用户问题：{q}

候选证据：
{items}
"""

COMPRESS_PROMPT = """从下面这段证据里，只摘出能回答用户问题的句子。

要求：
- 逐字摘录，不要改写，不要补充原文没有的内容。
- 保留小标题和代码符号。
- 如果这段证据和问题无关，只输出四个字：无相关内容。

用户问题：{q}

证据：
{text}
"""


def build_hybrid(index, bm25, question: str, cand: int = CAND):
    return rrf_fuse([
        index.as_retriever(similarity_top_k=cand).retrieve(question),
        bm25.retrieve(question, cand),
    ], top_k=cand, weights=WEIGHTS)


def llm_rerank(llm, question: str, hits, top_n: int) -> tuple[list, str, float]:
    items = "\n".join(
        f"{i}. [{n.metadata['file_path']}] {n.text.strip()[:220]}"
        for i, n in enumerate([h.node for h in hits], 1)
    )
    prompt = RERANK_PROMPT.format(n=len(hits), q=question, items=items)
    t0 = time.time()
    text, _ = ask(llm, prompt)
    seconds = time.time() - t0
    order = []
    m = re.search(r"\[[^\]]*\]", text)
    if m:
        try:
            order = [int(x) for x in json.loads(m.group(0))]
        except Exception:
            order = []
    order = [i for i in order if 1 <= i <= len(hits)]
    for i in range(1, len(hits) + 1):
        if i not in order:
            order.append(i)
    ranked = [hits[i - 1] for i in order][:top_n]
    return ranked, text.strip(), seconds


def compress(llm, question: str, hits) -> list:
    """把每段证据压到只剩相关句子，无关的整段丢掉。

    返回的是 NodeWithScore，好在下一步用同一套校验函数。
    """
    from llama_index.core.schema import NodeWithScore, TextNode

    kept = []
    for h in hits:
        text, _ = ask(llm, COMPRESS_PROMPT.format(q=question, text=h.node.text.strip()))
        text = text.strip()
        if "无相关内容" in text or len(text) < 8:
            continue
        kept.append(NodeWithScore(
            node=TextNode(text=text, metadata=dict(h.node.metadata)),
            score=h.score,
        ))
    return kept


def answer_once(llm, question: str, context: str) -> tuple[str, dict, float]:
    t0 = time.time()
    text, usage = ask(llm, build_prompt(question, context))
    return text, usage, time.time() - t0


def main() -> None:
    questions = load_questions()
    nodes = chunk(load_markdown_and_code(), "markdown", 384)
    index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                        embed_model=BGESmallZhEmbedding(), dim=512)
    bm25 = BM25Retriever(nodes)
    llm = build_llm(max_tokens=600)

    candidates: dict[str, list] = {}
    print("=" * 100)
    print("① 候选集里有没有 gold：没有的话，后面的重排再怎么调也补不回来")
    print("=" * 100)
    print(f"  {'题号':<6}{'候选20召回':<12}{'候选20命中':<12}{'候选3命中':<12}gold 所在位置")
    for q in questions:
        hits = build_hybrid(index, bm25, q["question"])
        candidates[q["id"]] = hits
        r20 = recall_at_k(hits, q["gold"])
        pos = [i for i, h in enumerate(hits, 1)
               if any(matches(h, g) for g in q["gold"])]
        print(f"  {q['id']:<6}{r20:<12}{hit_at_k(hits, q['gold']):<12}"
              f"{hit_at_k(hits[:TOP_K], q['gold']):<12}{pos or '不在候选集里'}")

    runs = {
        "A 检索直出": {"correct": 0, "citation_ok": 0, "unsupported": 0, "seconds": 0.0,
                       "prompt": 0, "rows": []},
        "B +Rerank": {"correct": 0, "citation_ok": 0, "unsupported": 0, "seconds": 0.0,
                      "prompt": 0, "rows": []},
        "C +Rerank+压缩": {"correct": 0, "citation_ok": 0, "unsupported": 0, "seconds": 0.0,
                           "prompt": 0, "rows": []},
    }

    print("\n" + "=" * 100)
    print("② 三种后处理逐题跑一遍")
    print("=" * 100)
    for q in questions:
        question = q["question"]
        hits = candidates[q["id"]]

        # A：直接取前 3
        top3 = hits[:TOP_K]
        ctx_a = assemble(top3)
        ans_a, usage_a, sec_a = answer_once(llm, question, ctx_a)
        ver_a = verify(ans_a, top3)

        # B：先重排
        ranked, raw, rerank_sec = llm_rerank(llm, question, hits, TOP_K)
        ctx_b = assemble(ranked)
        ans_b, usage_b, sec_b = answer_once(llm, question, ctx_b)
        ver_b = verify(ans_b, ranked)

        # C：重排 + 压缩
        t0 = time.time()
        compressed = compress(llm, question, ranked)
        compress_sec = time.time() - t0
        ctx_c = assemble(compressed)
        ans_c, usage_c, sec_c = answer_once(llm, question, ctx_c)
        ver_c = verify(ans_c, compressed)

        print(f"\n  {q['id']} {question}")
        print(f"      检索直出 top3：{[label(h) for h in top3]}")
        print(f"      重排后  top3：{[label(h) for h in ranked]}")
        print(f"      压缩后保留 {len(compressed)} 段，"
              f"{len(ctx_c)} 字符（压缩前 {len(ctx_b)}）")

        for key, ans, ver, usage, sec in [
            ("A 检索直出", ans_a, ver_a, usage_a, sec_a),
            ("B +Rerank", ans_b, ver_b, usage_b, sec_b + rerank_sec),
            ("C +Rerank+压缩", ans_c, ver_c, usage_c, sec_c + rerank_sec + compress_sec),
        ]:
            f = fact_score(ans, q["required_facts"], q["forbidden_facts"])
            agg = runs[key]
            agg["correct"] += 1 if f["correct"] else 0
            agg["citation_ok"] += 1 if ver["citation_ok"] else 0
            agg["unsupported"] += len(ver["unsupported"])
            agg["seconds"] += sec
            agg["prompt"] += usage.get("prompt_tokens") or 0
            agg["rows"].append({
                "id": q["id"], "correct": f["correct"], "missing": f["missing"],
                "forbidden": f["forbidden"], "citation_ok": ver["citation_ok"],
                "verdict": ver["verdict"], "unsupported": [e for _, e in ver["unsupported"]],
                "answer": ans,
            })
            print(f"      {key:<14} 正确={'是' if f['correct'] else '否'}"
                  f"{' 缺' + str(f['missing']) if f['missing'] else ''}"
                  f"  引用={ver['verdict']}  无依据实体={len(ver['unsupported'])}"
                  f"  耗时={sec:.1f}s")

    print("\n" + "=" * 100)
    print("③ 汇总")
    print("=" * 100)
    header = ["配置", "答案正确率", "引用合格率", "无依据实体", "总耗时s", "总prompt_tokens"]
    widths = [18, 12, 12, 12, 10, 16]
    print("  " + "".join(h.ljust(w) for h, w in zip(header, widths)))
    for key, agg in runs.items():
        n = len(questions)
        row = {
            "配置": key,
            "答案正确率": f"{agg['correct']}/{n}",
            "引用合格率": f"{agg['citation_ok']}/{n}",
            "无依据实体": agg["unsupported"],
            "总耗时s": round(agg["seconds"], 1),
            "总prompt_tokens": agg["prompt"],
        }
        print("  " + "".join(str(row[h]).ljust(w) for h, w in zip(header, widths)))

    print("\n  逐题正确性对比（√ 正确 / × 不正确）：")
    print(f"  {'题号':<6}{'A':<6}{'B':<6}{'C':<6}缺失的必备事实")
    for q in questions:
        cells = []
        miss = ""
        for key in runs:
            row = next(r for r in runs[key]["rows"] if r["id"] == q["id"])
            cells.append("√" if row["correct"] else "×")
            if not row["correct"] and not miss:
                miss = str(row["missing"] or row["forbidden"])
        print(f"  {q['id']:<6}" + "".join(c.ljust(6) for c in cells) + miss)

    print("\n  压缩把哪些证据整段丢掉了：")
    for q in questions[:3]:
        print(f"    {q['id']} 见上面每题的“压缩后保留 N 段”")


if __name__ == "__main__":
    main()
