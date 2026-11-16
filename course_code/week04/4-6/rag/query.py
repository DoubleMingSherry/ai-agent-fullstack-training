"""查询脚本（服务第二部分）：带参数调用 RAG，混合检索 + 生成带引用的回答。

检索方式（参考 4-4）：
  hybrid（默认）= BGE 向量 + BM25 关键词各召回 candidate_k 条，RRF 融合取 top_k
  vector         = 只用 BGE 向量
  bm25           = 只用 BM25 关键词

用法：
  python query.py --question "会话的有效期是多少分钟？"
  python query.py -q "embo-01 的维度" --mode bm25 --top-k 3
  python query.py -q "..." --no-llm          # 只看召回，不调模型
  python query.py -q "..." --doc-type code   # 元数据过滤
  python query.py -q "..." --json            # 机器可读：答案 + 引用来源追溯（供工具/程序调用）

--json 只向 stdout 输出一个 JSON 对象，字段：
  question / mode / top_k / answer（--no-llm 或 LLM 失败时为 null）/ llm_error（正常时 null）
  citations[]：{ index, file, heading, score, snippet }，index 与 answer 里的 [编号] 一一对应。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llama_index.core.vector_stores import MetadataFilter, MetadataFilters

from rag.answer import assemble, build_prompt, label
from rag.config import DEFAULT_STORE
from rag.hybrid import BM25Retriever, hybrid_retrieve, load_bm25_nodes
from rag.store import bm25_path, load_index


def retrieve(index, bm25: BM25Retriever, args):
    filters = None
    if args.doc_type:
        filters = MetadataFilters(
            filters=[MetadataFilter(key="doc_type", value=args.doc_type)])
    if args.mode == "vector":
        return index.as_retriever(similarity_top_k=args.top_k,
                                  filters=filters).retrieve(args.question)
    if args.mode == "bm25":
        hits = bm25.retrieve(args.question, args.top_k)
        if filters:
            wanted = args.doc_type
            hits = [h for h in hits if h.node.metadata.get("doc_type") == wanted]
        return hits
    weights = None
    if args.weights:
        weights = [float(x) for x in args.weights.split(",")]
    return hybrid_retrieve(index, bm25, args.question, top_k=args.top_k,
                           candidate_k=args.candidate_k, weights=weights,
                           filters=filters)


def main() -> None:
    ap = argparse.ArgumentParser(description="BGE + BM25 混合检索 RAG 查询")
    ap.add_argument("--question", "-q", required=True, help="查询问题")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                    help=f"存储目录（默认 {DEFAULT_STORE}，需先跑 ingest.py）")
    ap.add_argument("--mode", choices=["hybrid", "vector", "bm25"],
                    default="hybrid", help="检索方式（默认 hybrid）")
    ap.add_argument("--top-k", type=int, default=5, help="最终证据条数（默认 5）")
    ap.add_argument("--candidate-k", type=int, default=20,
                    help="混合检索每路候选数（默认 20）")
    ap.add_argument("--weights", type=str, default="",
                    help="RRF 权重，如 0.7,0.3（向量,BM25）；默认等权")
    ap.add_argument("--doc-type", type=str, default="",
                    help="元数据过滤：doc / code / web / table / pdf")
    ap.add_argument("--no-llm", action="store_true",
                    help="只输出召回结果，不调用 LLM 生成")
    ap.add_argument("--show-evidence", action="store_true",
                    help="打印证据全文（默认只打印来源）")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="输出机器可读 JSON（答案 + 引用来源追溯），供程序/工具调用")
    args = ap.parse_args()

    if not bm25_path(args.store).exists():
        msg = f"存储目录不存在或未入库：{args.store}；请先运行 ingest.py"
        if args.as_json:
            print(json.dumps({"question": args.question, "mode": args.mode, "top_k": args.top_k,
                              "answer": None, "llm_error": None, "citations": [], "error": msg},
                             ensure_ascii=False))
            sys.exit(1)
        sys.exit(msg)

    index = load_index(args.store)
    bm25 = BM25Retriever(load_bm25_nodes(bm25_path(args.store)))

    hits = retrieve(index, bm25, args)
    if not hits:
        if args.as_json:
            print(json.dumps({"question": args.question, "mode": args.mode, "top_k": args.top_k,
                              "answer": None, "llm_error": None, "citations": [], "error": "没有召回任何证据。"},
                             ensure_ascii=False))
            sys.exit(0)
        sys.exit("没有召回任何证据。")

    if args.as_json:
        payload = {
            "question": args.question,
            "mode": args.mode,
            "top_k": args.top_k,
            "answer": None,
            "llm_error": None,
            "citations": [
                {
                    "index": i,
                    "file": hit.node.metadata.get("file_path", "?"),
                    "heading": hit.node.metadata.get("heading") or "(正文)",
                    "score": round(float(hit.score), 4),
                    "snippet": hit.node.text.strip()[:300],
                }
                for i, hit in enumerate(hits, 1)
            ],
        }
        if not args.no_llm:
            prompt = build_prompt(args.question, assemble(hits))
            try:
                from rag.llm import ask, build_llm

                payload["answer"] = ask(build_llm(), prompt)
            except Exception as exc:  # LLM 失败不影响引用追溯
                payload["llm_error"] = repr(exc)
        print(json.dumps(payload, ensure_ascii=False))
        return

    print("=" * 80)
    print(f"问题：{args.question}  |  模式：{args.mode}  |  top_k：{args.top_k}")
    print("=" * 80)
    print("召回证据：")
    for i, hit in enumerate(hits, 1):
        print(f"  [{i}] {label(hit)}  score={hit.score:.4f}")
        if args.show_evidence:
            text = hit.node.text.strip().replace("\n", "\n      ")
            print(f"      {text[:300]}")

    if args.no_llm:
        return

    context = assemble(hits)
    prompt = build_prompt(args.question, context)
    print("\n" + "=" * 80)
    print("生成回答（MiniMax）：")
    print("=" * 80)
    try:
        from rag.llm import ask, build_llm

        llm = build_llm()
        print(ask(llm, prompt))
    except Exception as exc:
        print(f"[LLM 调用失败，检索结果不受影响] {exc!r}")
        print("可用 --no-llm 只看召回，或检查 ~/.hermes/.env 的 MINIMAX_CN_API_KEY。")


if __name__ == "__main__":
    main()
