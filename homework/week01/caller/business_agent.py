"""示例调用方（业务 Agent）：只通过网关 HTTP 接口访问模型。

验证统一协议（不变量 3）：调用方只知道网关地址与网关协议
（平台逻辑模型名、模板 name+version、业务 Schema、LLMResponse、错误 envelope），
不感知也不需要感知后端供应商的任何凭证与地址。

``transport`` 仅供测试注入 httpx.ASGITransport（离线直调 FastAPI app），
生产环境留空走真实网络。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8000"


class GatewayCallError(RuntimeError):
    """网关统一错误协议的调用方侧映射：code/message/call_id。"""

    def __init__(self, code: str, message: str, call_id: str | None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.call_id = call_id


class BusinessAgent:
    """客服助手示例：普通问答 + 意图分类（结构化输出）。"""

    def __init__(
        self,
        gateway_url: str | None = None,
        caller_id: str = "business-agent",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.gateway_url = gateway_url or os.environ.get(
            "GATEWAY_URL", DEFAULT_GATEWAY_URL
        )
        self._transport = transport
        self._headers = {"X-Caller-Id": caller_id}

    async def answer(self, question: str, style: str = "专业", model: str = "chat-lite") -> str:
        """普通文本问答：answer 模板 v2。"""
        body = await self._post(
            "/chat",
            {
                "model": model,
                "prompt": {
                    "name": "answer",
                    "version": "2",
                    "variables": {"question": question, "style": style},
                },
                "messages": [{"role": "user", "content": question}],
            },
        )
        return body["text"]

    async def classify(self, text: str, model: str = "chat-lite") -> dict[str, Any]:
        """结构化输出：router 模板（带条件分支）+ 业务 Schema。"""
        body = await self._post(
            "/chat",
            {
                "model": model,
                "prompt": {
                    "name": "router",
                    "version": "1",
                    "variables": {"text": text, "strict": True},
                },
                "messages": [{"role": "user", "content": text}],
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["category"],
                },
            },
        )
        return body["data"]

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.gateway_url,
            headers=self._headers,
            transport=self._transport,
            timeout=30.0,
        ) as client:
            response = await client.post(path, json=payload)
        body = response.json()
        if "error" in body:
            err = body["error"]
            raise GatewayCallError(err["code"], err["message"], err.get("call_id"))
        response.raise_for_status()
        return body
