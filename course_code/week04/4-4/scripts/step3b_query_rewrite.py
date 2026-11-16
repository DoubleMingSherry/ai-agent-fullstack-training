"""第三步之二：检索不准时，先改问题。

输入：8 个真实问题，以及第三步选出的检索配置（BGE 70% + BM25 30%）。
动作：① 原问题直接检索，作为基线；
      ② Query Rewrite：让模型把问题改写成文档用语，再检索；
      ③ Query Decomposition：拆成子问题，各自检索后合并；
      ④ 多路改写：3 个角度的查询，RRF 融合；
      ⑤ 换一个没说清服务的问题，看澄清判断能不能拦住。
结果：标准输出里给出四种做法的召回率，以及改写前后的查询原文。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import label
from rag.evaluate import hit_at_k, load_questions, recall_at_k
from rag.ingest import build_index, chunk, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import build_llm
from rag.retrieve import BM25Retriever, rrf_fuse
from rag.rewrite import (clarify, decompose, expand_with_glossary,
                         multi_query, rewrite)

TOP_K = 3
CAND = 20
WEIGHTS = [0.7, 0.3]


def main() -> None:
    questions = load_questions()
    docs = load_markdown_and_code()
    nodes = chunk(docs, strategy="markdown", chunk_size=384)
    bge = BGESmallZhEmbedding()
    index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                        embed_model=bge, dim=512)
    bm25 = BM25Retriever(nodes)
    llm = build_llm(max_tokens=200)

    def hybrid(queries: list[str], k: int = TOP_K):
        """把每个查询的两路结果都丢进 RRF。多查询时按查询顺序依次相加。"""
        lists, weights = [], []
        for q in queries:
            lists.append(index.as_retriever(similarity_top_k=CAND).retrieve(q))
            weights.append(WEIGHTS[0])
            lists.append(bm25.retrieve(q, CAND))
            weights.append(WEIGHTS[1])
        return rrf_fuse(lists, top_k=k, weights=weights)

    def evaluate(name: str, fn) -> dict:
        rows = []
        for q in questions:
            hits = fn(q["question"])
            rows.append((q, hits, recall_at_k(hits, q["gold"]), hit_at_k(hits, q["gold"])))
        recall = round(sum(r[2] for r in rows) / len(rows), 3)
        hit = round(sum(r[3] for r in rows) / len(rows), 3)
        print(f"  {name:<24} 召回率={recall:<7} 命中率={hit}")
        return {"name": name, "recall": recall, "hit": hit, "rows": rows}

    print("=" * 96)
    print("① 基线：原问题直接检索")
    print("=" * 96)
    base = evaluate("原问题", lambda q: hybrid([q]))

    print("\n" + "=" * 96)
    print("② Query Rewrite：把口语问题改写成文档用语")
    print("=" * 96)
    cache: dict[str, str] = {}
    for q in questions:
        t0 = time.time()
        cache[q["question"]] = rewrite(llm, q["question"])
        print(f"  {q['id']} 原问题：{q['question']}")
        print(f"      改写为：{cache[q['question']]}   ({time.time() - t0:.1f}s)")
    rw = evaluate("改写后（替换原问题）", lambda q: hybrid([cache[q]]))
    rw_plus = evaluate("改写后（与原问题一起）", lambda q: hybrid([q, cache[q]]))

    print("\n" + "=" * 96)
    print("③ Query Decomposition：拆成子问题再合并")
    print("=" * 96)
    subs: dict[str, list[str]] = {}
    for q in questions:
        subs[q["question"]] = decompose(llm, q["question"])
        print(f"  {q['id']} {q['question']}")
        for s in subs[q["question"]]:
            print(f"      → {s}")
    dc = evaluate("分解后", lambda q: hybrid(subs[q]))

    print("\n" + "=" * 96)
    print("④ 多路改写：三个角度并行检索后融合")
    print("=" * 96)
    multis: dict[str, list[str]] = {}
    for q in questions:
        multis[q["question"]] = multi_query(llm, q["question"], 3)
        print(f"  {q['id']} " + " | ".join(multis[q["question"]]))
    mq = evaluate("多路改写后（替换）", lambda q: hybrid(multis[q]))
    mq_plus = evaluate("多路改写后（含原问题）", lambda q: hybrid([q] + multis[q]))

    print("\n" + "=" * 96)
    print("⑤ 术语扩展：不换句子，只补文档里真正出现的词")
    print("=" * 96)
    expanded: dict[str, str] = {}
    for q in questions:
        expanded[q["question"]] = expand_with_glossary(q["question"])
        if expanded[q["question"]] != q["question"]:
            print(f"  {q['id']} {q['question']}")
            print(f"      → {expanded[q['question']]}")
    gl = evaluate("术语扩展后", lambda q: hybrid([expanded[q]]))

    print("\n" + "=" * 96)
    print("⑥ 汇总：哪些做法真的把漏掉的题补回来了")
    print("=" * 96)
    for name, res in [("原问题", base), ("重写(替换)", rw), ("重写(合并)", rw_plus),
                      ("分解", dc), ("多路(替换)", mq), ("多路(合并)", mq_plus),
                      ("术语扩展", gl)]:
        missed = [q["id"] for q, _, _, hit in res["rows"] if not hit]
        print(f"  {name:<10} 召回率={res['recall']:<7} 完全命中={sum(1 for r in res['rows'] if r[2] == 1.0)}/{len(questions)} "
              f"仍漏：{missed or '无'}")

    for q in questions:
        b = next(r for r in base["rows"] if r[0]["id"] == q["id"])
        m = next(r for r in gl["rows"] if r[0]["id"] == q["id"])
        if b[3] != m[3] or b[2] != m[2]:
            print(f"\n  {q['id']} {q['question']}")
            print(f"      原问题 top1：{label(b[1][0])}  召回={b[2]}")
            print(f"      术语扩展 top1：{label(m[1][0])}  召回={m[2]}")

    print("\n" + "=" * 96)
    print("⑦ 查询澄清：问题没说清服务时先追问")
    print("=" * 96)
    for q in ["会话多久过期？", "登录会话的有效期是多少分钟？", "SESSION_TTL_SECONDS 是多少？"]:
        verdict, follow = clarify(llm, q)
        print(f"  {q}")
        print(f"      判断：{verdict}" + (f"  追问：{follow}" if follow else ""))


if __name__ == "__main__":
    main()
