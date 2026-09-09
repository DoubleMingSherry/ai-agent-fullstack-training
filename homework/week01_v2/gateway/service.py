"""Gateway orchestration — binds governance, execution, egress & observability.

Layer responsibilities concentrated here:

* governance  : whitelist routing + capability check, template resolve/render,
                rate limiting (checked AFTER whitelist, BEFORE execution)
* execution   : unified Provider calls with per-model retry budget
                (exponential backoff, at most ``max_retries`` retries per model)
                then capability-equivalent fallback; retries never cross the
                first emitted stream chunk
* egress      : double validation (layer 1 passed to adapter, layer 2 local)
                with at most ONE repair round
* observability: every call lands in the TraceStore with usage/cost/latency/TTFT
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field as dc_field
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional

from .errors import GatewayError
from .models import (
    ChatMessage,
    ChatRequest,
    LLMResponse,
    PromptTrace,
    TokenUsage,
)
from .providers.base import Provider, ProviderContent, ProviderDone, ProviderError, ProviderRequest
from .ratelimit import RateLimiter
from .registry import CAP_JSON_SCHEMA, CAP_TEXT, ModelRegistry
from .structured import validate_output_text
from .templates import TemplateRegistry
from .trace import CallTrace, TraceStore


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


DEFAULT_MAX_INPUT_TOKENS = 200_000  # 入站上下文预算默认值（字符估算，可注入覆盖）

#: output_truncated 错误信息：写明三个处方（缩短上下文 / 提高 max_tokens / 拆小任务）
TRUNCATED_PRESCRIPTIONS = (
    "model output was truncated by the upstream length/context limit; "
    "prescriptions: 1) shorten the input context 2) raise max_tokens "
    "3) split the task into smaller pieces（处方：缩短上下文 / 提高 max_tokens / 拆小任务）"
)


def truncated_error() -> GatewayError:
    return GatewayError("output_truncated", TRUNCATED_PRESCRIPTIONS)


def estimate_tokens(text: str) -> int:
    """字符数 → token 粗估：中文≈1 字 1 token，英文≈4 字符 1 token。"""
    if not text:
        return 0
    wide = sum(1 for ch in text if ord(ch) > 0x7F)
    ascii_count = len(text) - wide
    return wide + (ascii_count + 3) // 4


def estimate_input_tokens(
    system_prompt: Optional[str],
    messages: List[ChatMessage],
    json_schema: Optional[Dict[str, Any]],
) -> int:
    parts: List[str] = []
    if system_prompt:
        parts.append(system_prompt)
    for message in messages:
        parts.append(message.content)
    if json_schema is not None:
        parts.append(json.dumps(json_schema, ensure_ascii=False, sort_keys=True))
    return sum(estimate_tokens(part) for part in parts)


@dataclass
class ExecContext:
    """Per-call state produced by governance, consumed by execution."""

    call_id: str
    caller_id: str
    request: ChatRequest
    chain: List[str]
    prompt_trace: Optional[PromptTrace] = None
    system_prompt: Optional[str] = None
    chat_messages: List[ChatMessage] = dc_field(default_factory=list)
    attempts: int = 0
    model_used: Optional[str] = None
    adapter: Optional[str] = None
    started_at: float = dc_field(default_factory=time.perf_counter)
    ttft_ms: Optional[float] = None
    truncated: bool = False  # upstream 归一化截断标记（成功/失败都要落 Trace）
    trace: Optional[CallTrace] = None

    @property
    def needs_json(self) -> bool:
        return self.request.json_schema is not None


class GatewayService:
    def __init__(
        self,
        *,
        registry: ModelRegistry,
        templates: TemplateRegistry,
        providers: Mapping[str, Provider],
        trace_store: TraceStore,
        rate_limiter: RateLimiter,
        backoff_base: float = 1.0,
        max_retries: int = 3,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        sleep: Optional[Callable[[float], Any]] = None,
    ) -> None:
        self.registry = registry
        self.templates = templates
        self.providers = dict(providers)
        self.trace_store = trace_store
        self.rate_limiter = rate_limiter
        self.backoff_base = backoff_base
        self.max_retries = max_retries
        self.max_input_tokens = max_input_tokens
        self._sleep = sleep or _default_sleep  # async fn: await self._sleep(seconds)

    # ------------------------------------------------------------------
    # governance
    # ------------------------------------------------------------------
    def _provider_for(self, model: str) -> Provider:
        provider = self.providers.get(model)
        if provider is None:
            spec = self.registry.get(model)
            provider = self.providers.get(spec.adapter_type)
        if provider is None:
            raise GatewayError(
                "internal_error", f"no provider registered for model {model!r}"
            )
        return provider

    def begin(self, request: ChatRequest, caller_id: str = "default") -> ExecContext:
        """Whitelist -> template -> rate limit (fixed governance order)."""
        started = time.perf_counter()
        spec = self.registry.get(request.model)  # -> unknown_model

        # ---- prompt template resolve + sandbox render -------------------
        prompt_trace: Optional[PromptTrace] = None
        system_prompt: Optional[str] = None
        if request.prompt is not None:
            tpl = self.templates.resolve(request.prompt.name, request.prompt.version)
            rendered = tpl.render(request.prompt.variables)  # missing_prompt_variable
            prompt_trace = PromptTrace(name=tpl.name, version=tpl.version, hash=tpl.hash)
            system_prompt = rendered

        # fold system-role chat messages into the system prompt
        sys_parts = [p for p in (system_prompt,) if p]
        chat_messages: List[ChatMessage] = []
        for message in request.messages:
            if message.role == "system":
                sys_parts.append(message.content)
            else:
                chat_messages.append(message)
        system_prompt = "\n".join(sys_parts) if sys_parts else None

        # ---- 入站上下文预算守卫（校验前置：未渲染完/变量缺失先于它，此后
        #      预算拒绝发生在限流与执行层之前，也不占用配额）----
        estimated = estimate_input_tokens(system_prompt, chat_messages, request.json_schema)
        if estimated > self.max_input_tokens:
            # 与限流拒绝同等的可观测性：接口层拒绝也记 Trace（attempts=0）
            budget_ctx = ExecContext(
                call_id=uuid.uuid4().hex,
                caller_id=caller_id,
                request=request,
                chain=self.registry.fallback_chain(request.model),
                started_at=started,
            )
            budget_error = GatewayError(
                "invalid_request",
                f"estimated input ~{estimated} tokens exceeds the configured "
                f"context budget of {self.max_input_tokens} tokens; "
                "please shorten the input context or split the request",
                call_id=budget_ctx.call_id,
            )
            self._record_error(budget_ctx, budget_error, attempts=0)
            raise budget_error

        # ---- capability check across the whole candidate chain ----------
        needed = frozenset({CAP_JSON_SCHEMA if request.json_schema is not None else CAP_TEXT})
        chain = self.registry.fallback_chain(request.model)
        primary = self.registry.get(request.model)
        for model in chain:
            candidate_spec = self.registry.get(model)
            if not needed <= candidate_spec.capabilities:
                raise GatewayError(
                    "internal_error",
                    f"model {model!r} lacks required capability {sorted(needed)}",
                )
            if model != request.model:
                self.registry.ensure_fallback_capability(primary, model)

        # ---- per-model rate limit (interface refusal: attempts stay 0) --
        allowed, retry_after = self.rate_limiter.acquire(request.model)
        if not allowed:
            ctx = ExecContext(
                call_id=uuid.uuid4().hex,
                caller_id=caller_id,
                request=request,
                chain=chain,
                started_at=started,
            )
            error = GatewayError(
                "rate_limited",
                f"model {request.model!r} exceeded its per-minute quota",
                retry_after=retry_after,
                call_id=ctx.call_id,
            )
            self._record_error(ctx, error, attempts=0)
            raise error

        ctx = ExecContext(
            call_id=uuid.uuid4().hex,
            caller_id=caller_id,
            request=request,
            chain=chain,
            prompt_trace=prompt_trace,
            system_prompt=system_prompt,
            chat_messages=chat_messages,
            started_at=started,
        )
        trace = self.trace_store.new(ctx.call_id, caller_id, request.model)
        trace.prompt_name = prompt_trace.name if prompt_trace else None
        trace.prompt_version = prompt_trace.version if prompt_trace else None
        trace.prompt_hash = prompt_trace.hash if prompt_trace else None
        ctx.trace = trace
        return ctx

    # ------------------------------------------------------------------
    # observability helpers
    # ------------------------------------------------------------------
    def _finish_success(self, ctx: ExecContext, usage: TokenUsage) -> None:
        trace = ctx.trace
        if trace is None:
            return
        spec = self.registry.get(ctx.model_used or ctx.request.model)
        trace.model_used = ctx.model_used
        trace.adapter = ctx.adapter
        trace.attempts = ctx.attempts
        trace.status = "success"
        trace.truncated = ctx.truncated
        trace.usage = _usage_dict(usage)
        trace.cost = spec_cost(spec, usage)
        trace.latency_ms = (time.perf_counter() - ctx.started_at) * 1000.0
        trace.ttft_ms = ctx.ttft_ms
        trace.finished_at_ms = trace.started_at_ms + trace.latency_ms

    def _record_error(
        self, ctx: ExecContext, error: GatewayError, attempts: Optional[int] = None
    ) -> None:
        trace = ctx.trace
        if trace is None:
            trace = self.trace_store.new(ctx.call_id, ctx.caller_id, ctx.request.model)
            ctx.trace = trace
        trace.model_used = ctx.model_used
        trace.adapter = ctx.adapter
        trace.attempts = ctx.attempts if attempts is None else attempts
        trace.status = "error"
        trace.truncated = ctx.truncated
        trace.error_code = error.code
        trace.error_message = error.message
        trace.latency_ms = (time.perf_counter() - ctx.started_at) * 1000.0
        trace.finished_at_ms = trace.started_at_ms + trace.latency_ms

    def _provider_request(self, ctx: ExecContext, model: str) -> ProviderRequest:
        return ProviderRequest(
            model=model,
            system=ctx.system_prompt,
            messages=ctx.chat_messages,
            json_schema=ctx.request.json_schema,
            max_tokens=ctx.request.max_tokens,
            temperature=ctx.request.temperature,
        )

    # ------------------------------------------------------------------
    # execution — non-streaming /chat
    # ------------------------------------------------------------------
    async def execute_text(self, ctx: ExecContext) -> LLMResponse:
        started = time.perf_counter()
        try:
            provider_result = await self._complete_with_retry_fallback(ctx)
        except GatewayError as error:
            error.call_id = ctx.call_id
            self._record_error(ctx, error)
            raise
        # TTFT 只属于流式（执行开始 → 首个内容块）。非流式调用只有整段返回后
        # 内容才全部可用，不存在“首 Token”时刻，故 ttft_ms 保持 None ——
        # 避免把 E2E 延迟贴上 TTFT 标签误导读者（协议允许 Optional）。
        text = provider_result.text
        usage = provider_result.usage
        ctx.truncated = provider_result.truncated
        structured: Optional[Any] = None
        if ctx.truncated and ctx.needs_json:
            # 截断的结构化输出无法完成 Schema 校验：归因为 output_truncated，
            # 而不是 schema_validation_failed
            error = truncated_error()
            error.call_id = ctx.call_id
            self._record_error(ctx, error)
            raise error
        if ctx.needs_json:
            # second-layer local validation, at most one repair round
            try:
                structured = validate_output_text(text, ctx.request.json_schema or {})
            except GatewayError as error:
                error.call_id = ctx.call_id
                self._record_error(ctx, error)
                raise
        self._finish_success(ctx, usage)
        spec = self.registry.get(ctx.model_used or ctx.request.model)
        return LLMResponse(
            call_id=ctx.call_id,
            model=ctx.request.model,
            model_used=ctx.model_used or ctx.request.model,
            adapter=ctx.adapter or "",
            attempts=ctx.attempts,
            text=text,
            structured=structured,
            truncated=ctx.truncated,
            usage=usage,
            cost=spec_cost(spec, usage),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            ttft_ms=ctx.ttft_ms,
            prompt=ctx.prompt_trace,
        )

    async def _complete_with_retry_fallback(self, ctx: ExecContext) -> ProviderResult:
        """Per-model retry budget; then capability-equivalent fallback."""
        last_error: Optional[ProviderError] = None
        for candidate in ctx.chain:
            provider = self._provider_for(candidate)
            retries_done = 0
            while True:
                ctx.attempts += 1
                try:
                    result = await provider.complete(self._provider_request(ctx, candidate))
                    ctx.model_used = candidate
                    ctx.adapter = provider.adapter_name
                    return result
                except ProviderError as error:
                    last_error = error
                    if not error.retryable or retries_done >= self.max_retries:
                        break  # fallback to the next capability-equivalent model
                    delay = self.backoff_base * (2 ** retries_done)
                    retries_done += 1
                    await self._sleep(delay)
        raise GatewayError(
            _exhausted_code(len(ctx.chain)),
            "upstream retries and fallback all failed"
            + (f": {last_error.message}" if last_error else ""),
        )

    # ------------------------------------------------------------------
    # execution — streaming /stream
    # ------------------------------------------------------------------
    async def stream_events(self, ctx: ExecContext) -> AsyncIterator[Dict[str, Any]]:
        """Yield SSE-ready event dicts: content.delta ... content.done | response.failed.

        Retry/fallback is allowed only BEFORE the first content chunk is
        forwarded (invariant 2): once a delta reached the client, any failure
        ends the stream with ``response.failed`` — never a silent model swap.
        """
        parts: List[str] = []
        usage = TokenUsage()
        first_chunk_at: Optional[float] = None
        finished = False

        def _finalize_success() -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._finish_success(ctx, usage)

        def _finalize_error(error: GatewayError) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._record_error(ctx, error)

        def _usage_event(event: ProviderDone) -> None:
            usage.input_tokens = max(usage.input_tokens, event.usage.input_tokens)
            usage.output_tokens = max(usage.output_tokens, event.usage.output_tokens)
            usage.cache_read_input_tokens = max(
                usage.cache_read_input_tokens, event.usage.cache_read_input_tokens
            )
            usage.cache_creation_input_tokens = max(
                usage.cache_creation_input_tokens, event.usage.cache_creation_input_tokens
            )

        try:
            succeeded = False
            for candidate in ctx.chain:
                provider = self._provider_for(candidate)
                retries_done = 0
                exhausted_candidate = False
                while not succeeded and not exhausted_candidate:
                    ctx.attempts += 1
                    try:
                        async for event in provider.stream(
                            self._provider_request(ctx, candidate)
                        ):
                            if isinstance(event, ProviderContent):
                                if ctx.model_used is None:
                                    ctx.model_used = candidate
                                    ctx.adapter = provider.adapter_name
                                if first_chunk_at is None:
                                    first_chunk_at = time.perf_counter()
                                    ctx.ttft_ms = (first_chunk_at - ctx.started_at) * 1000.0
                                parts.append(event.delta)
                                yield {
                                    "event": "content.delta",
                                    "data": {"delta": event.delta},
                                }
                            elif isinstance(event, ProviderDone):
                                _usage_event(event)
                                ctx.truncated = ctx.truncated or event.truncated
                        if ctx.model_used is None:
                            ctx.model_used = candidate
                            ctx.adapter = provider.adapter_name
                        succeeded = True
                    except ProviderError as error:
                        if ctx.model_used is not None:
                            # first chunk already forwarded: no retry / no switch
                            gateway_error = GatewayError(
                                "upstream_error",
                                f"stream failed after first chunk on {candidate!r}: {error.message}",
                            )
                            yield {
                                "event": "response.failed",
                                "data": {
                                    "code": gateway_error.code,
                                    "message": gateway_error.message,
                                },
                            }
                            _finalize_error(gateway_error)
                            return
                        if error.retryable and retries_done < self.max_retries:
                            delay = self.backoff_base * (2 ** retries_done)
                            retries_done += 1
                            await self._sleep(delay)
                        else:
                            exhausted_candidate = True  # try next candidate

            if not succeeded:
                gateway_error = GatewayError(
                    _exhausted_code(len(ctx.chain)),
                    "upstream retries and fallback all failed (streaming)",
                )
                yield {
                    "event": "response.failed",
                    "data": {"code": gateway_error.code, "message": gateway_error.message},
                }
                _finalize_error(gateway_error)
                return

            # ---- success path: truncation first, then schema validation ----
            text = "".join(parts)
            if ctx.truncated and ctx.needs_json:
                # 结构化输出被长度截断：归因 output_truncated（非 schema 问题）
                error = truncated_error()
                yield {
                    "event": "response.failed",
                    "data": {"code": error.code, "message": error.message},
                }
                _finalize_error(error)
                return
            structured: Optional[Any] = None
            if ctx.needs_json:
                try:
                    structured = validate_output_text(text, ctx.request.json_schema or {})
                except GatewayError as error:
                    # invariant 1: unvalidated output is never a success
                    yield {
                        "event": "response.failed",
                        "data": {"code": error.code, "message": error.message},
                    }
                    _finalize_error(error)
                    return
            _finalize_success()
            spec = self.registry.get(ctx.model_used or ctx.request.model)
            payload = LLMResponse(
                call_id=ctx.call_id,
                model=ctx.request.model,
                model_used=ctx.model_used or ctx.request.model,
                adapter=ctx.adapter or "",
                attempts=ctx.attempts,
                text=text,
                structured=structured,
                truncated=ctx.truncated,
                usage=usage,
                cost=spec_cost(spec, usage),
                latency_ms=(time.perf_counter() - ctx.started_at) * 1000.0,
                ttft_ms=ctx.ttft_ms,
                prompt=ctx.prompt_trace,
            )
            yield {"event": "content.done", "data": payload.model_dump()}
        except Exception as exc:  # noqa: BLE001 - never leak raw errors mid-stream
            if not finished:
                gateway_error = GatewayError(
                    "internal_error", f"streaming failure: {exc}"
                )
                yield {
                    "event": "response.failed",
                    "data": {"code": gateway_error.code, "message": gateway_error.message},
                }
                _finalize_error(gateway_error)
        finally:
            if not finished:
                trace = ctx.trace
                if trace is not None:  # aborted (e.g. client disconnect)
                    trace.status = "aborted"
                    trace.truncated = ctx.truncated
                    trace.attempts = ctx.attempts
                    trace.latency_ms = (time.perf_counter() - ctx.started_at) * 1000.0
                    trace.finished_at_ms = trace.started_at_ms + trace.latency_ms


def _exhausted_code(chain_length: int) -> str:
    """有备用模型而仍全部失败 → fallback_exhausted；未配置备用 → upstream_error。"""
    return "fallback_exhausted" if chain_length > 1 else "upstream_error"


def spec_cost(spec: Any, usage: TokenUsage) -> float:
    """Cost = 单价 × Token；缓存命中 token 按折扣价计（真实供应商缓存读更便宜）。"""
    input_price = spec.input_price_per_million
    discount = float(getattr(spec, "cache_read_discount", 1.0))
    return (
        (usage.input_tokens + usage.cache_creation_input_tokens)
        / 1_000_000.0
        * input_price
        + usage.cache_read_input_tokens / 1_000_000.0 * input_price * discount
        + usage.output_tokens / 1_000_000.0 * spec.output_price_per_million
    )


def _usage_dict(usage: TokenUsage) -> Dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "total_input_tokens": usage.total_input_tokens,
    }
