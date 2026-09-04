"""执行层：Provider Adapter。

数据流：Pydantic Message → Provider → Chat Completions → str + Usage。

红线：
- SDK 的 completion 对象不离开 Provider，Gateway 其余部分只消费
  ``ProviderResult``（str + Usage）和 ``StreamChunk``，不与供应商类型耦合。
- Provider 建客户端时 ``max_retries=0``，关闭 SDK 隐式重试，重试只有
  Gateway 一层说了算。
- Provider 通过依赖注入接入：生产用 OpenAICompatibleProvider，
  测试注入 FakeProvider（tests/conftest.py），全程无真实网络。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from .protocol import Message


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ProviderRequest:
    """发往上游的规范化请求（模型名已映射为上游真实名）。"""

    model: str
    system: str
    messages: tuple[Message, ...]
    response_schema: dict[str, Any] | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResult:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class StreamChunk:
    delta: str = ""
    usage: Usage | None = None  # 末块携带用量


class ProviderUnavailableError(Exception):
    """可重试的上游故障：连接错误 / 超时 / 上游 429（is_retryable 白名单）。"""


class ProviderFailureError(Exception):
    """不可重试的上游故障（其他上游错误，重试无意义）。"""


@runtime_checkable
class Provider(Protocol):
    async def complete(self, request: ProviderRequest) -> ProviderResult: ...

    def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]: ...


def _messages_payload(request: ProviderRequest) -> list[dict[str, str]]:
    system = request.system
    if request.response_schema is not None:
        # 结构化输出：Schema 随请求带给上游（第一层校验），兼容 json_object 模式
        system += (
            "\n必须输出 JSON，并符合此 JSON Schema：\n"
            + json.dumps(request.response_schema, ensure_ascii=False)
        )
    payload = [{"role": "system", "content": system}]
    payload.extend({"role": m.role, "content": m.content} for m in request.messages)
    return payload


class OpenAICompatibleProvider:
    """OpenAI Compatible Provider：Base URL / 模型名 / API Key / 能力统一管理。"""

    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0) -> None:
        from openai import AsyncOpenAI  # 延迟导入：离线测试不依赖 SDK

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # 关闭 SDK 隐式重试，重试只有 Gateway 一层说了算
        )
        self._errors = self._import_errors()

    @staticmethod
    def _import_errors() -> dict[str, type[Exception]]:
        import openai

        return {
            "timeout": openai.APITimeoutError,
            "connection": openai.APIConnectionError,
            "rate_limit": openai.RateLimitError,
            "status": openai.APIStatusError,
        }

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        kwargs: dict[str, Any] = {}
        if request.response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            raw = await self._client.chat.completions.create(
                model=request.model,
                messages=_messages_payload(request),
                max_tokens=request.max_output_tokens,
                **kwargs,
            )
        except (
            self._errors["timeout"],
            self._errors["connection"],
            self._errors["rate_limit"],
        ) as exc:  # is_retryable 白名单：连接错误/超时/上游 429
            raise ProviderUnavailableError(str(exc)) from exc
        except self._errors["status"] as exc:
            raise ProviderFailureError(str(exc)) from exc

        text = raw.choices[0].message.content or ""
        usage = raw.usage
        return ProviderResult(
            text=text,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            ),
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {}
        if request.response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            raw_stream = await self._client.chat.completions.create(
                model=request.model,
                messages=_messages_payload(request),
                max_tokens=request.max_output_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
        except (
            self._errors["timeout"],
            self._errors["connection"],
            self._errors["rate_limit"],
        ) as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        except self._errors["status"] as exc:
            raise ProviderFailureError(str(exc)) from exc

        try:
            async for raw in raw_stream:
                delta = raw.choices[0].delta.content if raw.choices else None
                if delta:
                    yield StreamChunk(delta=delta)
                if getattr(raw, "usage", None) is not None:
                    yield StreamChunk(
                        usage=Usage(
                            input_tokens=getattr(raw.usage, "prompt_tokens", 0),
                            output_tokens=getattr(raw.usage, "completion_tokens", 0),
                        )
                    )
        except (
            self._errors["timeout"],
            self._errors["connection"],
            self._errors["rate_limit"],
        ) as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        except self._errors["status"] as exc:
            raise ProviderFailureError(str(exc)) from exc

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        api_key = os.environ["GATEWAY_PROVIDER_API_KEY"]
        base_url = os.environ.get("GATEWAY_PROVIDER_BASE_URL", "https://api.deepseek.com")
        return cls(api_key=api_key, base_url=base_url)
