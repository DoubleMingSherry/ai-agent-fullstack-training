"""把 MiniMax 的 Embedding 和 Chat 接进 LlamaIndex。

MiniMax 的 embeddings 接口不是 OpenAI 形状的：
请求体用 `texts` 和 `type`，返回体用 `vectors`。
所以这里继承 LlamaIndex 的 BaseEmbedding，自己拼请求。
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import List

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.types import LLMMetadata, MessageRole
from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI

from .config import API_BASE, EMBED_MODEL, LLM_MODEL, api_key

EMBED_URL = f"{API_BASE}/embeddings"


# 计费口径：MiniMax 按字符计费，这里只统计请求次数和文本长度，用于对比。
CALL_STATS = {"embed_calls": 0, "embed_texts": 0, "embed_chars": 0, "embed_seconds": 0.0}


def _post(url: str, payload: dict, timeout: int = 60, retries: int = 3) -> dict:
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # 网络抖动时退避重试
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {last!r}")


class MiniMaxEmbedding(BaseEmbedding):
    """MiniMax embo-01，1536 维。

    truncate_dim > 0 时把向量截断到该维度并重新做 L2 归一化，
    用来实测“维度砍掉以后召回怎么变”。
    """

    batch_size: int = 8
    truncate_dim: int = 0

    def _embed(self, texts: List[str], kind: str) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            t0 = time.time()
            body = _post(EMBED_URL, {"model": EMBED_MODEL, "texts": batch, "type": kind})
            CALL_STATS["embed_calls"] += 1
            CALL_STATS["embed_texts"] += len(batch)
            CALL_STATS["embed_chars"] += sum(len(t) for t in batch)
            CALL_STATS["embed_seconds"] += time.time() - t0
            if body.get("base_resp", {}).get("status_code", 0) != 0:
                raise RuntimeError(f"embedding error: {body['base_resp']}")
            out.extend(body["vectors"])
        if self.truncate_dim:
            out = [_truncate(v, self.truncate_dim) for v in out]
        return out

    # 入库用 type=db，查询用 type=query
    def _get_text_embedding(self, text: str) -> List[float]:
        return self._embed([text], "db")[0]

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts, "db")

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._embed([query], "query")[0]

    async def _aget_query_embedding(self, query: str) -> List[float]:  # pragma: no cover
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:  # pragma: no cover
        return self._get_text_embedding(text)


def _truncate(vec: List[float], dim: int) -> List[float]:
    """截断到前 dim 维，再重新归一化，让余弦相似度仍然可比。"""
    head = vec[:dim]
    norm = sum(x * x for x in head) ** 0.5 or 1.0
    return [x / norm for x in head]


class MiniMaxLLM(OpenAI):
    """MiniMax 的 chat 接口是 OpenAI 形状的，但模型名不在 OpenAI 的注册表里。

    直接继承 OpenAI 会踩两个坑：
      1. metadata 会去查注册表，MiniMax 的名字查不到，抛 ValueError；
      2. is_chat_model 查表返回 False，chat() 会退回旧的 /completions 端点。
    所以这里固定 context_window，并把 is_chat_model 写死 True。
    """

    @classmethod
    def class_name(cls) -> str:
        return "minimax_llm"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=1_000_000,
            num_output=self.max_tokens or -1,
            is_chat_model=True,
            is_function_calling_model=True,
            model_name=self.model,
            system_role=MessageRole.SYSTEM,
        )

    @property
    def _tokenizer(self):
        """tiktoken 认不出 MiniMax 的模型名，直接放弃本地分词。"""
        return None


def build_llm(temperature: float = 0.0, max_tokens: int = 800) -> OpenAI:
    """MiniMax 的 chat 接口是 OpenAI 形状的，自定义 api_base 直接接。"""
    return MiniMaxLLM(
        model=LLM_MODEL,
        api_base=API_BASE,
        api_key=api_key(),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120,
    )


def ask(llm, prompt: str) -> tuple[str, dict]:
    """发一次单轮请求，返回 (文本, 用量)。"""
    t0 = time.time()
    resp = llm.chat([ChatMessage(role="user", content=prompt)])
    usage = dict(getattr(resp.raw, "usage", {}) or {})
    usage["seconds"] = round(time.time() - t0, 3)
    return resp.message.content or "", usage
