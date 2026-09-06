"""Minimal business Agent — EXAMPLE CALLER for the mini LLM gateway.

This caller sits outside the gateway and talks to it ONLY through the gateway's
HTTP protocol (``/chat`` and ``/stream``).  It contains **no** vendor API key
and **no** vendor Base URL anywhere: the target address is the gateway's own
address (default ``http://127.0.0.1:8000``, overridable via ``GATEWAY_BASE_URL``)
and transport may be injected (tests use ``httpx.ASGITransport`` so they stay
offline).
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

DEFAULT_GATEWAY_BASE_URL = "http://127.0.0.1:8000"


class MiniAgent:
    """A toy Agent Loop that only depends on the unified gateway protocol."""

    def __init__(
        self,
        *,
        caller_id: str = "demo-agent",
        base_url: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 120.0,
    ) -> None:
        self.caller_id = caller_id
        self.base_url = base_url or os.getenv("GATEWAY_BASE_URL") or DEFAULT_GATEWAY_BASE_URL
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=timeout
        )

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        prompt: Optional[Dict[str, Any]] = None,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Plain (non-streaming) call — returns the unified LLMResponse."""
        body: Dict[str, Any] = {"model": model, "messages": messages}
        if prompt is not None:
            body["prompt"] = prompt
        if schema is not None:
            body["schema"] = schema
        response = await self._client.post(
            "/chat", json=body, headers={"X-Caller-Id": self.caller_id}
        )
        response.raise_for_status()
        return response.json()

    async def stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        prompt: Optional[Dict[str, Any]] = None,
        schema: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming call — yields the gateway's typed SSE events.

        Each frame arrives on the wire as ``event: <name>`` + ``data: <json>``;
        it is re-assembled here into {"event": name, "data": payload}.
        """
        body: Dict[str, Any] = {"model": model, "messages": messages}
        if prompt is not None:
            body["prompt"] = prompt
        if schema is not None:
            body["schema"] = schema
        async with self._client.stream(
            "POST", "/stream", json=body, headers={"X-Caller-Id": self.caller_id}
        ) as response:
            response.raise_for_status()
            name: Optional[str] = None
            async for line in response.aiter_lines():
                line = line.rstrip("\r")
                if line.startswith("event:"):
                    name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    yield {"event": name, "data": payload}
                    name = None

    async def close(self) -> None:
        await self._client.aclose()
