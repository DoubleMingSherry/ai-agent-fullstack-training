"""第四步补充：重排补不回没召回的证据。

只做一件事：把候选深度压到 3，让 q4 的权威出处落在候选集之外，
再看 Rerank 能不能把它找回来。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import label
from rag.evaluate import load_questions, matches
from rag.ingest import build_index, chunk, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import build_llm
from rag.retrieve import BM25Retriever, rrf_fuse
from step4_postprocess import llm_rerank

WEIGHTS = [0.7, 0.3]


def main() -> None:
    q = next(x for x in load_questions() if x["id"] == "q4")
    nodes = chunk(load_markdown_and_code(), "markdown", 384)
    index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                        embed_model=BGESmallZhEmbedding(), dim=512)
    bm25 = BM25Retriever(nodes)

    for cand in (3, 20):
        hits = rrf_fuse([
            index.as_retriever(similarity_top_k=cand).retrieve(q["question"]),
            bm25.retrieve(q["question"], cand),
        ], top_k=cand, weights=WEIGHTS)
        present = [i for i, h in enumerate(hits, 1)
                   if any(matches(h, g) for g in q["gold"])]
        print("=" * 90)
        print(f"候选深度 = {cand}")
        print(f"  问题：{q['question']}")
        print(f"  权威出处（gold）：{q['gold'][0]['file']} § {q['gold'][0]['heading']}")
        print(f"  在第 {present or '（不在候选集里）'} 位")
        for i, h in enumerate(hits[:3], 1):
            print(f"    [{i}] {label(h)}")

        ranked, raw, sec = llm_rerank(build_llm(max_tokens=300), q["question"], hits, 3)
        after = [i for i, h in enumerate(ranked, 1)
                 if any(matches(h, g) for g in q["gold"])]
        print(f"  Rerank 用了 {sec:.1f}s，重排后前三：")
        for i, h in enumerate(ranked, 1):
            print(f"    [{i}] {label(h)}")
        print(f"  重排后 gold 位置：{after or '（仍然不在前三）'}")
        if cand == 3 and not present:
            print("  结论：候选集里没有 gold，重排只能在现有候选里换顺序，补不回来。")


if __name__ == "__main__":
    main()
