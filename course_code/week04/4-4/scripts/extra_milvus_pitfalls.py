"""Milvus 的两个实操坑，分三次进程运行，留下真实回执。

用法（必须分进程跑，坑本身就出在"另一个进程"上）：

    python scripts/extra_milvus_pitfalls.py build    # 第一次建库
    python scripts/extra_milvus_pitfalls.py again    # 另一个进程用 overwrite=False 再插一遍
    python scripts/extra_milvus_pitfalls.py noload   # 另一个进程不 load 直接检索
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import label
from rag.config import MILVUS_URI
from rag.ingest import _load_if_needed, build_index, chunk, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding

COLLECTION = "login_demo_pitfall"
QUESTION = "登录会话的有效期是多少分钟？"


def nodes():
    return chunk(load_markdown_and_code(), "markdown", 384)[:3]


def row_count():
    from llama_index.vector_stores.milvus import MilvusVectorStore

    store = MilvusVectorStore(uri=MILVUS_URI, collection_name=COLLECTION, dim=512,
                              overwrite=False, similarity_metric="COSINE")
    _load_if_needed(store, COLLECTION)
    return store.client.get_collection_stats(COLLECTION)["row_count"]


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    emb = BGESmallZhEmbedding()

    if mode == "build":
        print("[进程 1] 用 overwrite=True 建库，写入 3 个节点")
        build_index(nodes(), collection=COLLECTION, overwrite=True, embed_model=emb, dim=512)
        print(f"        collection 行数：{row_count()}")
        return

    if mode == "again":
        print("[进程 2] 换一个进程，用 overwrite=False 把同样的节点再插一遍")
        index = build_index(nodes(), collection=COLLECTION, overwrite=False,
                            embed_model=emb, dim=512)
        print(f"        collection 行数：{row_count()}")
        print(f"        检索 top-3：")
        for i, h in enumerate(index.as_retriever(similarity_top_k=3).retrieve(QUESTION), 1):
            print(f"          [{i}] {label(h)}  node_id={h.node.node_id[:8]}")
        return

    if mode == "noload":
        from llama_index.vector_stores.milvus import MilvusVectorStore

        print("[进程 3] 换一个进程，打开同一个 .db，不 load 直接检索")
        store = MilvusVectorStore(uri=MILVUS_URI, collection_name=COLLECTION, dim=512,
                                  overwrite=False, similarity_metric="COSINE")
        try:
            store.client.search(collection_name=COLLECTION, data=[[0.0] * 512], limit=1)
            print("        检索成功，没有被拦")
        except Exception as exc:
            print(f"        {type(exc).__name__}: {exc}")
        return

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
