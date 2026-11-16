"""数据准备：解析 → 分块 → 写 Milvus。

第一步只用 Markdown 和 Python 源码跑通最小闭环；
第二步再把 PDF、HTML、表格加进来。
"""

from __future__ import annotations

import ast
import re
import csv
import io
from pathlib import Path
from typing import Iterable, List

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import (
    MarkdownNodeParser,
    SentenceSplitter,
    TokenTextSplitter,
)
from llama_index.core.schema import TextNode
from llama_index.vector_stores.milvus import MilvusVectorStore

from .config import COLLECTION, EMBED_DIM, MILVUS_URI, ROOT, CORPUS
from .minimax import MiniMaxEmbedding

EXT_TO_TYPE = {
    ".md": "doc",
    ".py": "code",
    ".html": "web",
    ".csv": "table",
    ".pdf": "pdf",
}


def _rel(path: Path) -> str:
    """统一用「相对 corpus 的路径」做标识，引用和评估都认这个。# """
    return str(path.relative_to(CORPUS))


# ---------------------------------------------------------------- 解析

def parse_front_matter(text: str) -> tuple[dict, str]:
    """把 Markdown 顶部的 YAML 头信息解析成元数据，正文里不留这段。

    格式：

        ---
        service: login_demo
        updated: 2026-10-01
        ---
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n")
    if len(parts) < 2:
        return {}, text
    end = None
    for i in range(1, len(parts)):
        if parts[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict = {}
    for line in parts[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    body = "\n".join(parts[end + 1:]).lstrip("\n")
    return meta, body


def _doc_from_path(path: Path, text: str) -> Document:
    meta, body = parse_front_matter(text)
    base = {
        "file_path": _rel(path),
        "doc_type": EXT_TO_TYPE[path.suffix],
        "source": path.suffix.lstrip("."),
    }
    # 元数据里只放标量，Milvus 的标量字段才能建索引并用来过滤。
    for key, value in meta.items():
        base[key] = value
    return Document(text=body, metadata=base, id_=_rel(path))


def load_markdown_and_code() -> List[Document]:
    """第一步的语料：Markdown 文档 + 项目源码 + 历史记录。"""
    docs: List[Document] = []
    for path in sorted(CORPUS.rglob("*")):
        if path.suffix not in {".md", ".py"}:
            continue
        if path.name.startswith("."):
            continue
        docs.append(_doc_from_path(path, path.read_text(encoding="utf-8")))
    return docs


def load_html(path: Path) -> str:
    """HTML：先去掉标签，只留正文和标题层级。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    lines = []
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if tag.name.startswith("h"):
            lines.append(f"{'#' * int(tag.name[1])} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def load_table(path: Path) -> str:
    """CSV：转成"每行一句话"的文本，并保留列名。"""
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    out = []
    for row in rows:
        cells = "；".join(f"{k}是{v}" for k, v in row.items() if v)
        out.append(cells + "。")
    return "\n".join(out)


def load_pdf(path: Path) -> str:
    """PDF：pypdf 逐页抽文字。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        pages.append(f"<!-- page {i} -->\n{text}")
    return "\n\n".join(pages)


def load_all() -> List[Document]:
    """第二步的语料：把 PDF、HTML、表格一起读进来。"""
    docs = load_markdown_and_code()
    for path in sorted(CORPUS.rglob("*")):
        if path.suffix == ".html":
            text = load_html(path)
        elif path.suffix == ".csv":
            text = load_table(path)
        elif path.suffix == ".pdf":
            text = load_pdf(path)
        else:
            continue
        docs.append(
            Document(
                text=clean_text(text),
                metadata={
                    "file_path": _rel(path),
                    "doc_type": EXT_TO_TYPE[path.suffix],
                    "source": path.suffix.lstrip("."),
                },
                id_=_rel(path),
            )
        )
    return docs


# ---------------------------------------------------------------- 清洗

def clean_text(text: str) -> str:
    """去掉解析残留：页码注释、连续空行、行尾空格、全角空格。"""
    text = re.sub(r"<!--\s*page\s*\d+\s*-->", "", text)
    text = text.replace("\u3000", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- 分块

def chunk_markdown(docs: Iterable[Document], chunk_size: int = 384,
                   overlap: int = 0) -> List[TextNode]:
    """第一步：Markdown 按标题切；代码整文件一个节点。

    Markdown 交给 LlamaIndex 的 MarkdownNodeParser，每个小节一个节点。
    注意：0.14 的 MarkdownNodeParser 只在 header_path 里记录*上级*标题，
    小节自己的标题要靠节点正文第一行取回来（见 _heading_of）。
    """
    parser = MarkdownNodeParser()
    inner = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    nodes = []
    for doc in docs:
        if doc.metadata.get("doc_type") == "code":
            node = TextNode(text=doc.text, metadata={**doc.metadata, "heading": "(整文件)"})
            nodes.append(node)
            continue
        for node in parser.get_nodes_from_documents([doc]):
            for sub in inner.get_nodes_from_documents([node]) or [node]:
                nodes.append(_decorate(sub, doc))
    return nodes


def chunk_paragraph(docs: Iterable[Document], max_chars: int = 400) -> List[TextNode]:
    """按段落切：以空行为界，段落过长再按句切。"""
    inner = SentenceSplitter(chunk_size=max_chars, chunk_overlap=0)
    nodes = []
    for doc in docs:
        parts = [p.strip() for p in re.split(r"\n\s*\n", doc.text) if p.strip()]
        for part in parts:
            if len(part) <= max_chars:
                chunks = [part]
            else:
                sub = TextNode(text=part, metadata=dict(doc.metadata))
                chunks = [n.text for n in inner.get_nodes_from_documents([sub]) or [sub]]
            for text in chunks:
                nodes.append(_decorate(TextNode(text=text, metadata=dict(doc.metadata)), doc))
    return nodes


def chunk_semantic(docs: Iterable[Document], embed_model, buffer_size: int = 1) -> List[TextNode]:
    """语义切：相邻句子向量距离超过阈值就断开。"""
    from llama_index.core.node_parser import SemanticSplitterNodeParser

    splitter = SemanticSplitterNodeParser(
        buffer_size=buffer_size,
        breakpoint_percentile_threshold=90,
        embed_model=embed_model,
    )
    nodes = []
    for doc in docs:
        for node in splitter.get_nodes_from_documents([doc]):
            nodes.append(_decorate(node, doc))
    return nodes


def _heading_of(text: str, header_path: str) -> str:
    """取小节标题：优先用正文第一行的标题，再拼上级路径。"""
    first = text.strip().splitlines()[0] if text.strip() else ""
    m = re.match(r"^#{1,6}\s+(.*)$", first)
    own = m.group(1).strip() if m else ""
    parents = header_path.strip("/").replace("/", " > ")
    if parents and own:
        return f"{parents} > {own}"
    return own or parents


def chunk_fixed(docs: Iterable[Document], chunk_size: int = 384,
                overlap: int = 0) -> List[TextNode]:
    """固定长度切：不认标题，只按 token 数切。"""
    splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    nodes = []
    for doc in docs:
        for node in splitter.get_nodes_from_documents([doc]):
            nodes.append(_decorate(node, doc))
    return nodes


def chunk_code_ast(docs: Iterable[Document]) -> List[TextNode]:
    """代码按 AST 切：一个函数（或一个顶层语句块）一个节点。"""
    nodes = []
    for doc in docs:
        if doc.metadata.get("doc_type") != "code":
            continue
        source = doc.text
        lines = source.splitlines()
        tree = ast.parse(source)
        spans = []
        for item in tree.body:
            if isinstance(item, ast.ClassDef):
                spans.append((item.lineno - 1, item.end_lineno, item.name, "class"))
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([item.lineno] + [d.lineno for d in item.decorator_list]) - 1
                spans.append((start, item.end_lineno, item.name, "function"))
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                spans.append((item.lineno - 1, item.end_lineno, "(模块变量)", "assign"))
        # 上界不能越过下一个语句块，否则会把注释和函数之间的空行算进来
        for idx, (start, end, name, kind) in enumerate(spans):
            limit = spans[idx + 1][0] if idx + 1 < len(spans) else len(lines)
            end = min(end, limit)
            text = "\n".join(lines[start:end]).strip()
            if not text:
                continue
            node = TextNode(
                text=text,
                metadata={
                    **{k: v for k, v in doc.metadata.items()},
                    "heading": name,
                    "kind": kind,
                    "lines": f"{start + 1}-{end}",
                },
            )
            nodes.append(node)
    return nodes


def _decorate(node: TextNode, doc: Document) -> TextNode:
    header = node.metadata.get("header_path", "") or ""
    node.metadata = {
        **doc.metadata,
        "heading": _heading_of(node.text, header),
        "header_path": header,
    }
    return node


def chunk(docs, strategy: str = "markdown", chunk_size: int = 384, overlap: int = 0,
          embed_model=None):
    if strategy == "markdown":
        return chunk_markdown(docs, chunk_size, overlap)
    if strategy == "fixed":
        return chunk_fixed(docs, chunk_size, overlap)
    if strategy == "paragraph":
        return chunk_paragraph(docs, chunk_size)
    if strategy == "semantic":
        return chunk_semantic(docs, embed_model or get_embedding())
    if strategy == "token":
        splitter = TokenTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
        out = []
        for doc in docs:
            for node in splitter.get_nodes_from_documents([doc]):
                out.append(_decorate(node, doc))
        return out
    if strategy == "ast":
        return chunk_code_ast(docs)
    raise ValueError(strategy)


# ---------------------------------------------------------------- 入库

def get_embedding(truncate_dim: int = 0) -> MiniMaxEmbedding:
    return MiniMaxEmbedding(model_name="embo-01", truncate_dim=truncate_dim)


def _load_if_needed(store: MilvusVectorStore, collection: str) -> None:
    """Milvus Lite 在进程退出时会把 collection 置为 released。

    再打开同一个 .db 时，必须先 load 才能检索，否则报
    "Collection ... is in state 'released'; call load() before search"。
    不存在的 collection 会抛异常，直接忽略。
    """
    try:
        store.client.load_collection(collection_name=collection)
    except Exception:
        pass


def build_index(nodes: List[TextNode], collection: str = COLLECTION,
                overwrite: bool = True, embed_model=None, dim: int = EMBED_DIM) -> VectorStoreIndex:
    embed_model = embed_model or get_embedding()
    Settings.embed_model = embed_model
    store = MilvusVectorStore(
        uri=MILVUS_URI,
        collection_name=collection,
        dim=dim,
        overwrite=overwrite,
        similarity_metric="COSINE",
    )
    if not overwrite:
        _load_if_needed(store, collection)
    index = VectorStoreIndex(
        nodes,
        storage_context=StorageContext.from_defaults(vector_store=store),
        embed_model=embed_model,
        show_progress=True,
    )
    return index


def load_index(collection: str = COLLECTION, embed_model=None) -> VectorStoreIndex:
    embed_model = embed_model or get_embedding()
    Settings.embed_model = embed_model
    store = MilvusVectorStore(
        uri=MILVUS_URI,
        collection_name=collection,
        dim=EMBED_DIM,
        overwrite=False,
        similarity_metric="COSINE",
    )
    _load_if_needed(store, collection)
    return VectorStoreIndex.from_vector_store(store, embed_model=embed_model)
