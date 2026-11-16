"""本地 Embedding：BAAI/bge-small-zh-v1.5（fastembed，ONNX，512 维）。

与 4-4 相同：BGE 中文检索模型要求查询侧加指令前缀、passage 侧不加，
fastembed 的 query_embed / passage_embed 已按模型配置处理好了。
"""

from __future__ import annotations

import os
from typing import List

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from llama_index.core.base.embeddings.base import BaseEmbedding

from .config import EMBED_MODEL

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding

        _MODEL = TextEmbedding(EMBED_MODEL)
    return _MODEL


class BGESmallZhEmbedding(BaseEmbedding):
    """本地模型，不产生 API 调用，延迟稳定。"""

    def _get_query_embedding(self, query: str) -> List[float]:
        vec = list(_model().query_embed([query]))[0]
        return [float(x) for x in vec]

    def _get_text_embedding(self, text: str) -> List[float]:
        vec = list(_model().passage_embed([text]))[0]
        return [float(x) for x in vec]

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return [[float(x) for x in vec] for vec in _model().passage_embed(texts)]

    async def _aget_query_embedding(self, query: str) -> List[float]:  # pragma: no cover
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:  # pragma: no cover
        return self._get_text_embedding(text)
