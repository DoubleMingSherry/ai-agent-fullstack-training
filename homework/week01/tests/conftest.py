"""测试设施：FakeProvider（可脚本化）+ ASGITransport 直调 FastAPI app。

离线约束：全程无真实网络请求；供应商凭证只在本文件以假值出现。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from gateway.app import Settings, create_app
from gateway.provider import (
    ProviderFailureError,
    ProviderRequest,
    ProviderResult,
    ProviderUnavailableError,
    StreamChunk,
    Usage,
)
from gateway.ratelimit import RateLimitConfig

# 可重试白名单：连接错误 / 超时 / 上游 429；server 属于不可重试
RETRYABLE_ERRORS = ("connect", "timeout", "rate_limit")


def fake_error(name: str) -> Exception:
    if name in RETRYABLE_ERRORS:
        return ProviderUnavailableError(f"fake {name} error")
    return ProviderFailureError(f"fake {name} error")


class FakeProvider:
    """按脚本逐次响应：可控制失败类型、chunk 数与内容、非法 JSON。

    脚本步骤：
      {"kind": "text", "text": str, "usage": Usage?}
      {"kind": "error", "error": "connect"|"timeout"|"rate_limit"|"server"}
      {"kind": "stream", "chunks": [str, ...], "usage": Usage?,
       "fail_after": int?}  # 发出 fail_after 个 chunk 后按 error 失败
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls: list[ProviderRequest] = []

    def _next(self, request: ProviderRequest) -> dict[str, Any]:
        self.calls.append(request)
        if not self.script:
            raise AssertionError("FakeProvider 脚本已耗尽")
        return self.script.pop(0)

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        step = self._next(request)
        if step["kind"] == "error":
            raise fake_error(step["error"])
        assert step["kind"] == "text", f"未知脚本步骤: {step}"
        return ProviderResult(
            text=step["text"], usage=step.get("usage", Usage(input_tokens=10, output_tokens=5))
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        step = self._next(request)
        assert step["kind"] == "stream", f"未知脚本步骤: {step}"
        chunks = step.get("chunks", [])
        fail_after = step.get("fail_after")
        for idx, delta in enumerate(chunks):
            if fail_after is not None and idx == fail_after:
                raise fake_error(step["error"])
            yield StreamChunk(delta=delta)
        if fail_after is not None and fail_after >= len(chunks):
            raise fake_error(step["error"])
        yield StreamChunk(usage=step.get("usage", Usage(input_tokens=7, output_tokens=3)))


def make_app(provider: FakeProvider, quota: int = 100):
    """创建注入 FakeProvider 的 app（治理配置可用小值注入）。"""
    return create_app(
        Settings(
            provider=provider,
            rate_limit=RateLimitConfig(quota=quota, window_seconds=60.0),
        )
    )


def make_transport(app) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


def call(app, method: str, url: str, **kwargs) -> httpx.Response:
    """同步封装：AsyncClient + ASGITransport 直调 app，无真实网络。"""

    async def _run() -> httpx.Response:
        transport = make_transport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(_run())


def post_json(app, url: str, payload: dict[str, Any] | None = None, headers: dict | None = None) -> httpx.Response:
    kwargs: dict[str, Any] = {"headers": headers or {}}
    if payload is not None:
        kwargs["json"] = payload
    return call(app, "POST", url, **kwargs)


def get_json(app, url: str, headers: dict | None = None) -> httpx.Response:
    return call(app, "GET", url, headers=headers or {})


def chat_payload(
    model: str = "chat-lite",
    prompt_name: str = "answer",
    version: str = "2",
    variables: dict[str, Any] | None = None,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": {
            "name": prompt_name,
            "version": version,
            "variables": variables
            if variables is not None
            else {"question": "什么是幂等？", "style": "专业"},
        },
        "messages": [{"role": "user", "content": "请回答上面的问题"}],
    }
    if response_schema is not None:
        payload["response_schema"] = response_schema
    return payload


def stream_once(app, payload: dict[str, Any]) -> tuple[int, list[tuple[str, dict]]]:
    """执行一次 /stream 请求，返回 (状态码, SSE 事件列表)。"""

    async def _run() -> tuple[int, list[tuple[str, dict]]]:
        transport = make_transport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
            async with client.stream("POST", "/stream", json=payload) as response:
                body = (await response.aread()).decode("utf-8")
                return response.status_code, parse_sse(body)

    return asyncio.run(_run())


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        event = "message"
        data = ""
        for line in lines:
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        events.append((event, json.loads(data)))
    return events


def deltas_of(events: list[tuple[str, dict]]) -> list[str]:
    return [data["delta"] for name, data in events if name == "content.delta"]


def first_error(events: list[tuple[str, dict]]) -> dict | None:
    for name, data in events:
        if name == "response.failed":
            return data["error"]
    return None
