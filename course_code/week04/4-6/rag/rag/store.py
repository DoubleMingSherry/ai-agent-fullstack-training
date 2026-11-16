"""向量库持久化：SimpleVectorStore 落盘 / 加载（参考 4-4）。

用 SimpleVectorStore（JSON 文件）替代 Milvus：零服务依赖，
ingest.py 写完以后 query.py 打开同一个目录即可。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from llama_index.core import (
    Settings,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.node_parser import (
    MarkdownNodeParser,
    SentenceSplitter,
)
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import SimpleVectorStore

from .local_embed import BGESmallZhEmbedding

BM25_FILE = "bm25_nodes.pkl"


def get_embedding() -> BGESmallZhEmbedding:
    return BGESmallZhEmbedding()


# ---------------------------------------------------------------- 分块（同 4-4：Markdown 按标题切，代码整文件一个节点）

def _heading_of(text: str, header_path: str) -> str:
    """取小节标题：优先用正文第一行的标题，再拼上级路径。"""
    import re

    first = text.strip().splitlines()[0] if text.strip() else ""
    m = re.match(r"^#{1,6}\s+(.*)$", first)
    own = m.group(1).strip() if m else ""
    parents = header_path.strip("/").replace("/", " > ")
    if parents and own:
        return f"{parents} > {own}"
    return own or parents


def _decorate(node: TextNode, doc) -> TextNode:
    header = node.metadata.get("header_path", "") or ""
    node.metadata = {
        **doc.metadata,
        "heading": _heading_of(node.text, header),
        "header_path": header,
    }
    return node


def chunk_documents(docs, chunk_size: int = 384, overlap: int = 0) -> List[TextNode]:
    """Markdown 交给 MarkdownNodeParser 按标题切；代码整文件一个节点。"""
    parser = MarkdownNodeParser()
    inner = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    nodes: List[TextNode] = []
    for doc in docs:
        if doc.metadata.get("doc_type") == "code":
            nodes.append(TextNode(
                text=doc.text,
                metadata={**doc.metadata, "heading": "(整文件)"},
            ))
            continue
        for node in parser.get_nodes_from_documents([doc]):
            for sub in inner.get_nodes_from_documents([node]) or [node]:
                nodes.append(_decorate(sub, doc))
    return nodes


# ---------------------------------------------------------------- 建库 / 开库

def build_and_save(nodes: List[TextNode], store_dir: Path,
                   overwrite: bool = True) -> VectorStoreIndex:
    """BGE 向量化写入 SimpleVectorStore，落盘到 store_dir；nodes 供 BM25 用。"""
    if store_dir.exists() and not overwrite:
        raise RuntimeError(f"{store_dir} 已存在；加 --overwrite 覆盖")
    embed_model = get_embedding()
    Settings.embed_model = embed_model
    store = SimpleVectorStore()
    index = VectorStoreIndex(
        nodes,
        storage_context=StorageContext.from_defaults(vector_store=store),
        embed_model=embed_model,
        show_progress=True,
    )
    store_dir.mkdir(parents=True, exist_ok=True)
    # 整个存储目录落盘（default_vector_store.json + docstore.json 等）
    index.storage_context.persist(str(store_dir))
    return index


def load_index(store_dir: Path) -> VectorStoreIndex:
    """从落盘目录重建索引（向量数据直接读，不重新 embedding）。"""
    embed_model = get_embedding()
    Settings.embed_model = embed_model
    ctx = StorageContext.from_defaults(persist_dir=str(store_dir))
    return load_index_from_storage(ctx, embed_model=embed_model)


def bm25_path(store_dir: Path) -> Path:
    return store_dir / BM25_FILE
