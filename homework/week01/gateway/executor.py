"""执行层：重试与 fallback 编排。

次数契约：恰好 1 次重试 + 至多 1 次 fallback。
- is_retryable 是白名单：连接错误/超时/上游 429 才重试（ProviderUnavailableError）。
- 白名单故障：当前模型重试恰好 1 次；重试仍失败则进入能力等价备用模型
  （备用模型同样有 1 次重试机会）。
- 备用模型再失败返回明确错误码 fallback_exhausted，不得继续换模型。
- fallback 时点红线（不变量 2）：仅首 Token（首 chunk）发出前可 fallback；
  首块发出后失败则结束流并产生 response.failed，不能换模型拼接重写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from .errors import GatewayError
from .models import ModelSpec
from .provider import (
    Provider,
    ProviderFailureError,
    ProviderRequest,
    ProviderResult,
    ProviderUnavailableError,
    Usage,
)


@dataclass(frozen=True)
class ChatExecution:
    result: ProviderResult  # str + Usage，SDK completion 对象不出 Provider
    model_used: str
    attempts: int


# ---- 流式事件（Gateway 内部协议，路由层负责转成 SSE） ----


@dataclass(frozen=True)
class StreamDelta:
    text: str


@dataclass(frozen=True)
class StreamCompleted:
    model_used: str
    attempts: int
    usage: Usage
    text: str


@dataclass(frozen=True)
class StreamFailed:
    code: str
    message: str
    model_used: str | None
    attempts: int


def _with_model(request: ProviderRequest, spec: ModelSpec) -> ProviderRequest:
    return ProviderRequest(
        model=spec.upstream,
        system=request.system,
        messages=request.messages,
        response_schema=request.response_schema,
        max_output_tokens=request.max_output_tokens,
    )


async def execute_chat(
    provider: Provider,
    chain: list[ModelSpec],
    request: ProviderRequest,
) -> ChatExecution:
    """普通调用：按主备链执行，白名单故障各获 1 次重试。"""
    attempts = 0
    for spec in chain:
        retried = False
        while True:
            attempts += 1
            try:
                result = await provider.complete(_with_model(request, spec))
                return ChatExecution(
                    result=result, model_used=spec.name, attempts=attempts
                )
            except ProviderUnavailableError:
                if not retried:  # 恰好 1 次重试
                    retried = True
                    continue
                break  # 本模型配额用尽 → 换能力等价备用模型
            except ProviderFailureError:
                break  # 不可重试故障：不重试，直接换备用模型
    raise GatewayError(
        _exhausted_code(chain),
        "上游模型重试与 fallback 均失败",
        attempts=attempts,
        model_used=chain[-1].name,
    )


async def execute_stream(
    provider: Provider,
    chain: list[ModelSpec],
    request: ProviderRequest,
) -> AsyncIterator[StreamDelta | StreamCompleted | StreamFailed]:
    """流式调用：首块发出前遵守与普通调用相同的重试/fallback 契约；
    首块发出后的任何失败直接 response.failed，不再换模型。"""
    attempts = 0
    last_spec: ModelSpec | None = None
    for spec in chain:
        last_spec = spec
        retried = False
        while True:
            attempts += 1
            gen = provider.stream(_with_model(request, spec))
            emitted = False
            parts: list[str] = []
            usage = Usage()
            try:
                async for chunk in gen:
                    if chunk.delta:
                        emitted = True
                        parts.append(chunk.delta)
                        yield StreamDelta(chunk.delta)
                    if chunk.usage is not None:
                        usage = chunk.usage
                yield StreamCompleted(
                    model_used=spec.name,
                    attempts=attempts,
                    usage=usage,
                    text="".join(parts),
                )
                return
            except Exception as exc:
                if emitted:
                    # 不变量 2：首块已出，不能换模型重写，只能报错收尾
                    yield StreamFailed(
                        code="upstream_error",
                        message=f"流式输出中途失败: {exc}",
                        model_used=spec.name,
                        attempts=attempts,
                    )
                    return
                if isinstance(exc, ProviderUnavailableError) and not retried:
                    retried = True
                    continue
                break
            finally:
                await gen.aclose()
    yield StreamFailed(
        code=_exhausted_code(chain),
        message="上游模型重试与 fallback 均失败",
        model_used=last_spec.name if last_spec else None,
        attempts=attempts,
    )



def _exhausted_code(chain: list[ModelSpec]) -> str:
    # 有备用模型而仍失败 → 明确的 fallback_exhausted；未配置备用 → upstream_error
    return "fallback_exhausted" if len(chain) > 1 else "upstream_error"
