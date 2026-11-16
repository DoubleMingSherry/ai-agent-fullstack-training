"""生成侧 LLM：MiniMax chat 接口（OpenAI 形状），沿用 4-4 的接法。

直接继承 OpenAI 会踩两个坑（见 4-4/rag/minimax.py）：
  1. metadata 会去查模型注册表，MiniMax 的名字查不到，抛 ValueError；
  2. is_chat_model 查表返回 False，chat() 会退回旧的 /completions 端点。
所以这里固定 context_window，并把 is_chat_model 写死 True。
"""

from __future__ import annotations

import time

from llama_index.core.base.llms.types import LLMMetadata, MessageRole
from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI

from .config import API_BASE, LLM_MODEL, api_key


class MiniMaxLLM(OpenAI):
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
    return MiniMaxLLM(
        model=LLM_MODEL,
        api_base=API_BASE,
        api_key=api_key(),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120,
    )


def ask(llm, prompt: str) -> str:
    """发一次单轮请求，返回文本回答。"""
    resp = llm.chat([ChatMessage(role="user", content=prompt)])
    return resp.message.content or ""
