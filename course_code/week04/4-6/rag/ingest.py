"""入库脚本（服务第一部分）：文档 → 向量数据库 + BM25 索引。

流程（参考 4-4）：
  语料加载（md/py/html/csv/pdf）→ Markdown 按标题切、代码整文件一个节点
  → BGE 本地 embedding 写入 SimpleVectorStore 落盘
  → 同一批节点 pickle 落盘，供 BM25 查询时使用。

用法：
  python ingest.py                          # 默认吃本目录的 corpus/，写 storage/
  python ingest.py --corpus path/to/docs --store path/to/store --overwrite
  （path/to/docs、path/to/store 是占位符，要换成真实存在的目录）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag.config import DEFAULT_CORPUS, DEFAULT_STORE
from rag.docload import load_all
from rag.hybrid import save_bm25_nodes
from rag.store import bm25_path, build_and_save, chunk_documents


def main() -> None:
    ap = argparse.ArgumentParser(description="文档 → BGE 向量库 + BM25 索引")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS,
                    help=f"语料目录（默认 {DEFAULT_CORPUS}）")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                    help=f"存储目录（默认 {DEFAULT_STORE}）")
    ap.add_argument("--chunk-size", type=int, default=384,
                    help="Markdown 小节过长时的二次切分粒度（默认 384）")
    ap.add_argument("--overwrite", action="store_true",
                    help="存储目录已存在时覆盖重建")
    args = ap.parse_args()

    if not args.corpus.exists():
        sys.exit(f"语料目录不存在：{args.corpus}")
    if args.store.exists() and any(args.store.iterdir()) and not args.overwrite:
        sys.exit(f"存储目录非空：{args.store}；要重建请加 --overwrite")

    t0 = time.time()
    docs = load_all(args.corpus)
    print(f"[加载] {len(docs)} 个文档（来源：{args.corpus}）")
    for doc in docs:
        print(f"  - {doc.metadata['file_path']}  ({doc.metadata['doc_type']})")

    nodes = chunk_documents(docs, chunk_size=args.chunk_size)
    print(f"[分块] {len(nodes)} 个节点")

    build_and_save(nodes, args.store, overwrite=True)
    save_bm25_nodes(nodes, bm25_path(args.store))
    print(f"[完成] 向量库 + BM25 索引已写入 {args.store}"
          f"（用时 {time.time() - t0:.1f}s）")
    print("  查询请用：python query.py --question \"...\"")


if __name__ == "__main__":
    main()
