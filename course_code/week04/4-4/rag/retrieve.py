"""检索：向量、BM25、Hybrid（RRF）、Metadata Filter。

三种检索都返回 llama_index 的 NodeWithScore，后面的装配和评估不用分情况处理。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

import jieba
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.vector_stores import (FilterOperator, MetadataFilter,
                                             MetadataFilters)
from rank_bm25 import BM25Okapi

jieba.setLogLevel(60)


# ---------------------------------------------------------------- 向量

def vector_retrieve(index, query: str, top_k: int = 5,
                    filters: MetadataFilters | None = None) -> List[NodeWithScore]:
    retriever = index.as_retriever(similarity_top_k=top_k, filters=filters)
    return retriever.retrieve(query)


def service_filter(service: str) -> MetadataFilters:
    """按 front-matter 里的 service 字段过滤。"""
    return MetadataFilters(filters=[MetadataFilter(key="service", value=service)])


def doc_type_filter(doc_type: str) -> MetadataFilters:
    return MetadataFilters(
        filters=[MetadataFilter(key="doc_type", value=doc_type)],
        condition="and",
    )


def not_table_filter() -> MetadataFilters:
    return MetadataFilters(
        filters=[MetadataFilter(key="doc_type", value="table",
                                operator=FilterOperator.NE)],
    )


# ---------------------------------------------------------------- BM25

def tokenize(text: str) -> List[str]:
    """中文用 jieba 切，英文和代码符号按原样保留。"""
    return [t for t in jieba.lcut(text.lower()) if t.strip()]


class BM25Retriever:
    """关键词检索：不依赖向量，符号名和命令能精确命中。"""

    def __init__(self, nodes: Sequence[TextNode]):
        self.nodes = list(nodes)
        self.corpus = [tokenize(n.text) for n in self.nodes]
        self.bm25 = BM25Okapi(self.corpus)

    def retrieve(self, query: str, top_k: int = 5) -> List[NodeWithScore]:
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [NodeWithScore(node=self.nodes[i], score=float(scores[i])) for i in order]


# ---------------------------------------------------------------- Hybrid

def rrf_fuse(result_lists: Iterable[Sequence[NodeWithScore]], top_k: int = 5,
             k: int = 60, weights: Sequence[float] | None = None) -> List[NodeWithScore]:
    """倒数排名融合：只看名次，不看两边的分数。

    RRF 的分数 = Σ weight / (k + rank)，rank 从 1 开始。
    因为向量分和 BM25 分不在一个量纲上，直接相加没有意义。
    weights 让召回更准的一路占更大比重；不传就是等权。
    """
    lists = list(result_lists)
    if weights is None:
        weights = [1.0] * len(lists)
    fused: dict[str, float] = {}
    keep: dict[str, TextNode] = {}
    for weight, results in zip(weights, lists):
        for rank, item in enumerate(results, 1):
            key = item.node.node_id
            fused[key] = fused.get(key, 0.0) + weight / (k + rank)
            keep.setdefault(key, item.node)
    order = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [NodeWithScore(node=keep[key], score=score) for key, score in order]
