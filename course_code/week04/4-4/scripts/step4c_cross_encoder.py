"""第四步补充：Cross Encoder 重排，和 LLM 重排放在同一批候选上比。

输入：8 个问题 + 第四步那套检索（BGE + BM25，加权 RRF，候选深度 20，不含术语扩展）。
动作：同一批候选，分别用本地 Cross Encoder（BAAI/bge-reranker-base）和 LLM 重排，
      各取前三，算召回率和命中率。
结果：标准输出给出三种做法的对比，以及 q4 的重排细节。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llama_index.core.schema import NodeWithScore

from rag.answer import label
from rag.evaluate import hit_at_k, load_questions, recall_at_k
from rag.ingest import build_index, chunk, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import build_llm
from rag.retrieve import BM25Retriever, rrf_fuse
from step4_postprocess import llm_rerank

CAND = 20
TOP_K = 3
WEIGHTS = [0.7, 0.3]
CE_MODEL = "BAAI/bge-reranker-base"


def main() -> None:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    questions = load_questions()
    nodes = chunk(load_markdown_and_code(), "markdown", 384)
    index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                        embed_model=BGESmallZhEmbedding(), dim=512)
    bm25 = BM25Retriever(nodes)
    llm = build_llm(max_tokens=300)

    t0 = time.time()
    ce = TextCrossEncoder(CE_MODEL)
    load_seconds = time.time() - t0
    print(f"[模型] {CE_MODEL}，加载 {load_seconds:.1f}s（首次运行含下载）")

    def candidates(question: str):
        return rrf_fuse([
            index.as_retriever(similarity_top_k=CAND).retrieve(question),
            bm25.retrieve(question, CAND),
        ], top_k=CAND, weights=WEIGHTS)

    configs = {"直出前三": [], "LLM 重排": [], "Cross Encoder 重排": []}
    ce_seconds = 0.0
    llm_seconds = 0.0
    detail = {}

    for q in questions:
        hits = candidates(q["question"])

        t0 = time.time()
        ranked_llm, _, _ = llm_rerank(llm, q["question"], hits, TOP_K)
        llm_seconds += time.time() - t0

        t0 = time.time()
        scores = list(ce.rerank(q["question"], [h.node.text for h in hits]))
        ce_seconds += time.time() - t0
        order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)[:TOP_K]
        ranked_ce = [NodeWithScore(node=hits[i].node, score=float(scores[i])) for i in order]

        for name, ranked in [("直出前三", hits[:TOP_K]),
                             ("LLM 重排", ranked_llm),
                             ("Cross Encoder 重排", ranked_ce)]:
            configs[name].append({
                "id": q["id"],
                "recall": recall_at_k(ranked, q["gold"]),
                "hit": hit_at_k(ranked, q["gold"]),
                "top1": label(ranked[0]) if ranked else "(空)",
            })
        detail[q["id"]] = {"question": q["question"], "hits": hits,
                           "llm": ranked_llm, "ce": ranked_ce, "gold": q["gold"]}

    print("\n" + "=" * 96)
    print("三种做法在同一批候选（20 条）上的结果，都取前三")
    print("=" * 96)
    header = ["配置", "召回率@3", "命中率@3", "完全命中题数"]
    widths = [22, 10, 10, 14]
    print("  " + "".join(h.ljust(w) for h, w in zip(header, widths)))
    for name, rows in configs.items():
        n = len(rows)
        print("  " + name.ljust(22)
              + f"{round(sum(r['recall'] for r in rows) / n, 3):<10}"
              + f"{round(sum(r['hit'] for r in rows) / n, 3):<10}"
              + f"{sum(1 for r in rows if r['recall'] == 1.0)}/{n}")

    print("\n逐题对照（√ = 该题 gold 进了前三）：")
    print("  " + "题号".ljust(6) + "".join(n.ljust(34) for n in configs))
    for q in questions:
        cells = []
        for name in configs:
            r = next(x for x in configs[name] if x["id"] == q["id"])
            cells.append(("√ " if r["hit"] else "× ") + r["top1"][:28])
        print("  " + q["id"].ljust(6) + "".join(c.ljust(34) for c in cells))

    print("\nq4 的重排细节（权威出处在候选里排第 20 位）：")
    d = detail["q4"]
    print(f"  问题：{d['question']}")
    print(f"  Cross Encoder 打分的前三条：")
    for i, h in enumerate(d["ce"], 1):
        print(f"    [{i}] {label(h)}  score={h.score:.3f}")
    print(f"  LLM 重排的前三条：")
    for i, h in enumerate(d["llm"], 1):
        print(f"    [{i}] {label(h)}")
    pos_ce = [i for i, h in enumerate(d["ce"], 1)
              if any(g["file"] in label(h) and g["heading"] in label(h) for g in d["gold"])]
    print(f"  gold 在 Cross Encoder 结果里的位置：{pos_ce or '（不在前三）'}")

    n = len(questions)
    print(f"\n[耗时] Cross Encoder 重排合计 {ce_seconds:.2f}s"
          f"（平均 {ce_seconds / n:.2f}s/题）；LLM 重排合计 {llm_seconds:.1f}s"
          f"（平均 {llm_seconds / n:.1f}s/题）")


if __name__ == "__main__":
    main()
