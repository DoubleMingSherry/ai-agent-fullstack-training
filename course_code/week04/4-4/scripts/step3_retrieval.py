"""第三步之一：检索本身能召回多少。

输入：8 个真实问题（eval/questions.json）+ 按标题切好的节点。
动作：① 向量检索、BM25、Hybrid(RRF) 各跑一遍 top-3，比召回；
      ② 用 front-matter 里的 service 字段做 Metadata Filter；
      ③ 把 1536 维向量截到 768/256/128 维重跑，看维度对召回的影响。
结果：标准输出里给出逐题命中情况和三种配置的汇总指标。
这一节只测检索，不调用大模型，所以没有答案正确率。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import label
from rag.config import COLLECTION
from rag.evaluate import hit_at_k, load_questions, recall_at_k
from rag.ingest import build_index, chunk, get_embedding, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import CALL_STATS
from rag.retrieve import BM25Retriever, rrf_fuse, service_filter

TOP_K = 3



def summarize(name: str, rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "配置": name,
        "召回率": round(sum(r["recall"] for r in rows) / n, 3),
        "命中率": round(sum(r["hit"] for r in rows) / n, 3),
        "完全命中题数": sum(1 for r in rows if r["recall"] == 1.0),
        "问题数": n,
    }


def run_config(name: str, retriever_fn, questions: list[dict]) -> list[dict]:
    rows = []
    for q in questions:
        hits = retriever_fn(q["question"], TOP_K)
        rows.append({
            "id": q["id"],
            "recall": recall_at_k(hits, q["gold"]),
            "hit": hit_at_k(hits, q["gold"]),
            "top1": label(hits[0]) if hits else "(空)",
            "top3": [label(h) for h in hits],
        })
    return rows


def main() -> None:
    questions = load_questions()
    docs = load_markdown_and_code()
    nodes = chunk(docs, strategy="markdown", chunk_size=384)
    print(f"[准备] {len(docs)} 个文档 → {len(nodes)} 个节点（按标题切）")
    print(f"[问题集] {len(questions)} 题，每题都标了 gold 证据")

    index = build_index(nodes, collection=COLLECTION, overwrite=True,
                        embed_model=get_embedding())
    bm25 = BM25Retriever(nodes)

    print("\n" + "=" * 96)
    print("① 向量 / BM25 / Hybrid 在同一批问题上的逐题结果（top-3）")
    print("=" * 96)

    configs = {
        "向量": lambda q, k: index.as_retriever(similarity_top_k=k).retrieve(q),
        "BM25": lambda q, k: bm25.retrieve(q, k),
        "RRF等权候选3": lambda q, k: rrf_fuse([
            index.as_retriever(similarity_top_k=k).retrieve(q),
            bm25.retrieve(q, k),
        ], top_k=k),
        "RRF等权候选10": lambda q, k: rrf_fuse([
            index.as_retriever(similarity_top_k=10).retrieve(q),
            bm25.retrieve(q, 10),
        ], top_k=k),
        "RRF加权候选10": lambda q, k: rrf_fuse([
            index.as_retriever(similarity_top_k=10).retrieve(q),
            bm25.retrieve(q, 10),
        ], top_k=k, weights=[0.3, 1.0]),
    }

    summaries = []
    detail = {}
    for name, fn in configs.items():
        rows = run_config(name, fn, questions)
        detail[name] = rows
        summaries.append(summarize(name, rows))

    header = ["配置", "召回率", "命中率", "完全命中题数", "问题数"]
    widths = [16, 8, 8, 14, 8]
    print("  " + "".join(h.ljust(w) for h, w in zip(header, widths)))
    for s in summaries:
        print("  " + "".join(str(s[h]).ljust(w) for h, w in zip(header, widths)))

    print("\n逐题对比（√ = 该题的 gold 进了 top-3）：")
    names = list(configs)
    print("  " + "题号".ljust(6) + "".join(n.ljust(20) for n in names))
    for q in questions:
        cells = []
        for name in names:
            row = next(r for r in detail[name] if r["id"] == q["id"])
            cells.append(("√ " if row["hit"] else "× ") + row["top1"][:16])
        print("  " + q["id"].ljust(6) + "".join(c.ljust(20) for c in cells))

    print("\n四种配置互相不一致的题：")
    for q in questions:
        flags = {n: next(r for r in detail[n] if r["id"] == q["id"])["hit"] for n in names}
        if len(set(flags.values())) > 1:
            print(f"  {q['id']} {q['question'][:40]}")
            for n in names:
                row = next(r for r in detail[n] if r["id"] == q["id"])
                print(f"      {n:<14} {'命中' if row['hit'] else '漏掉'} → {row['top1']}")

    # ---------------------------------------------------------------- 过滤
    print("\n" + "=" * 96)
    print("② Metadata Filter：用 front-matter 的 service 字段把干扰文档挡在外面")
    print("=" * 96)
    q = "会话的有效期是多少分钟？"
    plain = index.as_retriever(similarity_top_k=5).retrieve(q)
    filtered = index.as_retriever(
        similarity_top_k=5, filters=service_filter("login_demo")).retrieve(q)
    print(f"query = {q}")
    print("  不过滤：")
    for i, h in enumerate(plain, 1):
        print(f"    [{i}] {label(h)}  score={h.score:.4f}")
    print("  过滤 service=login_demo：")
    for i, h in enumerate(filtered, 1):
        print(f"    [{i}] {label(h)}  score={h.score:.4f}")

    # ------------------------------------------------------- 模型与维度
    print("\n" + "=" * 96)
    print("③ Embedding 模型与维度：召回从哪里来")
    print("=" * 96)
    model_rows = []
    for name, emb, dim in [
        ("MiniMax embo-01", get_embedding(), 1536),
        ("MiniMax 截断", get_embedding(truncate_dim=256), 256),
        ("BGE-small-zh 本地", BGESmallZhEmbedding(), 512),
    ]:
        coll = "login_demo_" + name.split()[0].lower().replace("-", "_") + str(dim)
        t0 = time.time()
        idx = build_index(nodes, collection=coll, overwrite=True, embed_model=emb, dim=dim)
        rows = run_config(name, lambda q, k, idx=idx: idx.as_retriever(
            similarity_top_k=k).retrieve(q), questions)
        s = summarize(name, rows)
        s["维度"] = dim
        s["建库秒"] = round(time.time() - t0, 1)
        s["本地"] = "否" if "MiniMax" in name else "是"
        model_rows.append(s)

    header2 = ["配置", "维度", "召回率", "命中率", "完全命中题数", "建库秒", "本地"]
    widths2 = [18, 6, 8, 8, 14, 8, 6]
    print("  " + "".join(h.ljust(w) for h, w in zip(header2, widths2)))
    for s in model_rows:
        print("  " + "".join(str(s[h]).ljust(w) for h, w in zip(header2, widths2)))
    bm25_summary = summarize("BM25（参考）", detail["BM25"])
    print(f"  {'BM25（参考）':<18}{'-':<6}{bm25_summary['召回率']:<8}{bm25_summary['命中率']:<8}"
          f"{bm25_summary['完全命中题数']}/{bm25_summary['问题数']:<11}{'-':<8}{'是'}")

    print(f"\n[embedding 调用] MiniMax 侧 {CALL_STATS['embed_calls']} 次请求、"
          f"{CALL_STATS['embed_texts']} 条文本、{CALL_STATS['embed_seconds']:.1f}s；"
          f"BGE 本地不产生 API 调用")

    # ------------------------------------------------------- 定最终配置
    print("\n" + "=" * 96)
    print("④ 换好模型再混合：BGE + BM25 的各种组合")
    print("=" * 96)
    bge = BGESmallZhEmbedding()
    bge_index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                            embed_model=bge, dim=512)

    combos = {
        "BGE 单独": lambda q, k: bge_index.as_retriever(similarity_top_k=k).retrieve(q),
        "BGE+BM25 等权候选10": lambda q, k: rrf_fuse([
            bge_index.as_retriever(similarity_top_k=10).retrieve(q), bm25.retrieve(q, 10)
        ], top_k=k),
        "BGE+BM25 等权候选20": lambda q, k: rrf_fuse([
            bge_index.as_retriever(similarity_top_k=20).retrieve(q), bm25.retrieve(q, 20)
        ], top_k=k),
        "BGE 70% + BM25 30%": lambda q, k: rrf_fuse([
            bge_index.as_retriever(similarity_top_k=20).retrieve(q), bm25.retrieve(q, 20)
        ], top_k=k, weights=[0.7, 0.3]),
    }
    rows_all = []
    for name, fn in combos.items():
        rows = run_config(name, fn, questions)
        s = summarize(name, rows)
        rows_all.append(s)
        print(f"  {name:<22} 召回率={s['召回率']:<7} 命中率={s['命中率']:<7} "
              f"完全命中={s['完全命中题数']}/{s['问题数']}")

    best_name = max(rows_all, key=lambda s: (s["召回率"], s["命中率"]))["配置"]
    print(f"\n  本批问题上的最好配置：{best_name}")
    best_rows = run_config(best_name, combos[best_name], questions)
    print("  这个配置仍然漏掉的题：")
    for q in questions:
        row = next(r for r in best_rows if r["id"] == q["id"])
        if not row["hit"]:
            print(f"    {q['id']} {q['question'][:36]} → top1 {row['top1']}")


if __name__ == "__main__":
    main()
