"""文档加载：Markdown / Python / HTML / CSV / PDF → LlamaIndex Document（解析逻辑沿用 4-4）。"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import List

from llama_index.core import Document

EXT_TO_TYPE = {
    ".md": "doc",
    ".py": "code",
    ".html": "web",
    ".csv": "table",
    ".pdf": "pdf",
}


def _rel(path: Path, corpus: Path) -> str:
    """统一用「相对 corpus 的路径」做标识，引用直接认这个。"""
    return str(path.relative_to(corpus))


# ---------------------------------------------------------------- 清洗

def clean_text(text: str) -> str:
    """去掉解析残留：页码注释、连续空行、行尾空格、全角空格。"""
    text = re.sub(r"<!--\s*page\s*\d+\s*-->", "", text)
    text = text.replace("\u3000", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- 各格式解析

def parse_front_matter(text: str) -> tuple[dict, str]:
    """把 Markdown 顶部的 YAML 头信息解析成元数据，正文里不留这段。"""
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


# ---------------------------------------------------------------- 入口

def load_all(corpus: Path) -> List[Document]:
    """递归读取 corpus 下支持的文档，全部转成 Document（带元数据）。"""
    docs: List[Document] = []
    for path in sorted(corpus.rglob("*")):
        if path.suffix not in EXT_TO_TYPE or path.name.startswith("."):
            continue
        base = {
            "file_path": _rel(path, corpus),
            "doc_type": EXT_TO_TYPE[path.suffix],
            "source": path.suffix.lstrip("."),
        }
        if path.suffix in {".md", ".py"}:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
            # 元数据里只放标量，向量库的标量字段才能建索引并用来过滤
            docs.append(Document(text=body, metadata={**base, **meta},
                                 id_=base["file_path"]))
        elif path.suffix == ".html":
            text = load_html(path)
            docs.append(Document(text=clean_text(text), metadata=base,
                                 id_=base["file_path"]))
        elif path.suffix == ".csv":
            text = load_table(path)
            docs.append(Document(text=clean_text(text), metadata=base,
                                 id_=base["file_path"]))
        elif path.suffix == ".pdf":
            text = load_pdf(path)
            docs.append(Document(text=clean_text(text), metadata=base,
                                 id_=base["file_path"]))
    return docs
