"""接口层：FastAPI 应用工厂与三个入口。

- POST /chat  普通调用，返回统一 LLMResponse
- POST /stream 流式调用，text/event-stream；首块已发出后的失败以
  response.failed 事件结束流（HTTP envelope 只覆盖首块之前）
- GET  /trace[/​{call_id}]  Trace 列表与详情

统一错误 envelope：``{error: {code, message, call_id}}``。
Provider 通过应用工厂注入（生产 from_env 真实 Provider，测试注入 FakeProvider）。
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import GatewayError, error_envelope
from .executor import StreamCompleted, StreamDelta, StreamFailed, execute_chat, execute_stream
from .models import ModelCatalog, default_catalog
from .prompts import TemplateRegistry, default_templates, render, validate_variables
from .provider import Provider, ProviderRequest, Usage
from .protocol import ChatRequest, LLMResponse, PromptUsed, UsageOut
from .ratelimit import RateLimitConfig, RateLimiter
from .structured import build_validator, validate_structured
from .streamjson import IncrementalJsonParser
from .trace import TraceStore, new_trace


@dataclass
class Settings:
    """组合根：Provider / 治理配置 / Trace 存储都在这里装配。"""

    provider: Provider
    models: ModelCatalog = field(default_factory=default_catalog)
    templates: TemplateRegistry = field(default_factory=default_templates)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    traces: TraceStore = field(default_factory=TraceStore)
    rate_limiter: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self.rate_limiter = RateLimiter(self.rate_limit)

    @classmethod
    def production(cls) -> "Settings":
        from .provider import OpenAICompatibleProvider  # 延迟导入：离线测试不依赖 SDK

        return cls(provider=OpenAICompatibleProvider.from_env())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.production()
    app = FastAPI(title="mini-llm-gateway")
    app.state.settings = settings

    def caller_of(request: Request) -> str:
        return request.headers.get("x-caller-id") or "default"

    def fail_trace(trace: dict[str, Any], exc: GatewayError, latency_ms: float) -> None:
        trace["status"] = "failed"
        trace["error_code"] = exc.code
        trace["latency_ms"] = round(latency_ms, 3)
        if exc.attempts:
            trace["attempts"] = exc.attempts
        if exc.model_used:
            trace["model_used"] = exc.model_used
        settings.traces.add(trace)

    # ---- 统一错误 envelope ----

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        call_id = exc.call_id or new_call_id()  # envelope 保证携带 call_id
        return JSONResponse(
            status_code=exc.status,
            content=error_envelope(exc.code, exc.message, call_id),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        call_id = new_call_id()
        trace = new_trace(call_id, caller_of(request), requested_model="")
        trace["error_code"] = "invalid_request"
        settings.traces.add(trace)
        first = exc.errors()[0].get("msg", "请求校验失败") if exc.errors() else "请求校验失败"
        return JSONResponse(
            status_code=400,
            content=error_envelope("invalid_request", f"请求协议校验失败: {first}", call_id),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        call_id = new_call_id()
        detail = exc.detail if isinstance(exc.detail, str) else "HTTP 错误"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope("http_error", detail, call_id),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        call_id = new_call_id()
        trace = new_trace(call_id, caller_of(request), requested_model="")
        trace["error_code"] = "internal_error"
        settings.traces.add(trace)
        return JSONResponse(
            status_code=500,
            content=error_envelope("internal_error", f"网关内部错误: {exc}", call_id),
        )

    # ---- 入口协议的公共前置：限流 → 白名单 → 模板治理 → Schema 编译 ----

    def prepare(req: ChatRequest, trace: dict[str, Any], caller_id: str) -> tuple:
        """依次通过治理层校验，返回 (chain, ProviderRequest, validator)。

        任一失败抛 GatewayError：此时首块必然未发出，HTTP envelope 照常适用。
        """
        allowed, retry_after = settings.rate_limiter.check(caller_id)
        if not allowed:
            raise GatewayError(
                "rate_limited",
                f"调用方 {caller_id} 超出窗口配额，请稍后重试",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
        model_spec = settings.models.validate(req.model)  # unknown_model
        tpl = settings.templates.get(req.prompt.name, req.prompt.version)
        trace["prompt"] = {
            "name": tpl.name,
            "version": tpl.version,
            "hash": tpl.hash,
        }
        validate_variables(tpl, req.prompt.variables)  # missing/invalid_prompt_variable
        system = render(tpl, req.prompt.variables)
        validator = (
            build_validator(req.response_schema) if req.response_schema else None
        )
        preq = ProviderRequest(
            model=model_spec.upstream,
            system=system,
            messages=tuple(req.messages),
            response_schema=req.response_schema,
        )
        return settings.models.chain(req.model), preq, validator

    def usage_out(usage: Usage, model_used: str) -> UsageOut:
        spec = settings.models.validate(model_used)  # 定价表随白名单维护
        return UsageOut(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost=spec.cost(usage.input_tokens, usage.output_tokens),
        )

    def fill_usage(trace: dict[str, Any], usage: UsageOut) -> None:
        trace["input_tokens"] = usage.input_tokens
        trace["output_tokens"] = usage.output_tokens
        trace["cost"] = usage.cost

    # ---- POST /chat ----

    @app.post("/chat", response_model=LLMResponse)
    async def chat(req: ChatRequest, request: Request) -> LLMResponse:
        call_id = new_call_id()
        caller_id = caller_of(request)
        trace = new_trace(call_id, caller_id, req.model)
        started = time.monotonic()
        exec_started = 0.0
        try:
            chain, preq, validator = prepare(req, trace, caller_id)
            exec_started = time.monotonic()
            execution = await execute_chat(settings.provider, chain, preq)
            result = execution.result
            data = None
            if validator is not None:
                data = validate_structured(result.text, req.response_schema, validator)
            usage = usage_out(result.usage, execution.model_used)
            trace.update(
                model_used=execution.model_used,
                attempts=execution.attempts,
                status="ok",
            )
            fill_usage(trace, usage)
            trace["latency_ms"] = round((time.monotonic() - exec_started) * 1000, 3)
            settings.traces.add(trace)
            return LLMResponse(
                call_id=call_id,
                model_used=execution.model_used,
                text=result.text,
                data=data,
                usage=usage,
                prompt=PromptUsed(**trace["prompt"]),
                attempts=execution.attempts,
                latency_ms=trace["latency_ms"],
            )
        except GatewayError as exc:
            exc.call_id = call_id  # envelope 携带本次调用的 call_id
            latency = time.monotonic() - (exec_started or started)
            fail_trace(trace, exc, latency * 1000)
            raise

    # ---- POST /stream ----

    @app.post("/stream")
    async def stream(req: ChatRequest, request: Request) -> StreamingResponse:
        call_id = new_call_id()
        caller_id = caller_of(request)
        trace = new_trace(call_id, caller_id, req.model)
        started = time.monotonic()
        try:
            chain, preq, validator = prepare(req, trace, caller_id)
        except GatewayError as exc:
            exc.call_id = call_id
            fail_trace(trace, exc, (time.monotonic() - started) * 1000)
            raise  # 首块未发出：仍适用 HTTP envelope

        async def event_stream() -> AsyncIterator[str]:
            exec_started = time.monotonic()
            parser = IncrementalJsonParser()
            finalized = False

            def finish_failure(exc: GatewayError) -> None:
                nonlocal finalized
                if not finalized:
                    finalized = True
                    fail_trace(trace, exc, (time.monotonic() - exec_started) * 1000)

            try:
                async for ev in execute_stream(settings.provider, chain, preq):
                    if isinstance(ev, StreamDelta):
                        yield sse_event(
                            "content.delta", {"call_id": call_id, "delta": ev.text}
                        )
                        if validator is not None:
                            # 增量解析：不等全部 chunk 到齐，完成一个顶层键就上报
                            for key, value in parser.feed(ev.text):
                                yield sse_event(
                                    "json.partial",
                                    {"call_id": call_id, "key": key, "value": value},
                                )
                    elif isinstance(ev, StreamCompleted):
                        usage = usage_out(ev.usage, ev.model_used)
                        data = None
                        if validator is not None:
                            try:
                                data = validate_structured(
                                    ev.text, req.response_schema, validator
                                )
                            except GatewayError as exc:
                                finish_failure(exc)
                                yield sse_event("response.failed", failed_payload(call_id, exc))
                                return
                        trace.update(
                            model_used=ev.model_used,
                            attempts=ev.attempts,
                            status="ok",
                        )
                        fill_usage(trace, usage)
                        trace["latency_ms"] = round(
                            (time.monotonic() - exec_started) * 1000, 3
                        )
                        finalized = True
                        settings.traces.add(trace)
                        yield sse_event(
                            "response.completed",
                            {
                                "call_id": call_id,
                                "model_used": ev.model_used,
                                "attempts": ev.attempts,
                                "usage": usage.model_dump(),
                                "data": data,
                            },
                        )
                    elif isinstance(ev, StreamFailed):
                        exc = GatewayError(ev.code, ev.message)
                        exc.attempts = ev.attempts
                        exc.model_used = ev.model_used
                        finish_failure(exc)
                        yield sse_event("response.failed", failed_payload(call_id, exc))
                        return
            finally:
                # 兜底：客户端断开等未走完的流，Trace 以失败收口
                finish_failure(
                    GatewayError("upstream_error", "流未正常结束（客户端断开或网关异常）")
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # ---- GET /trace ----

    @app.get("/trace")
    async def list_traces(limit: int = 100) -> dict[str, Any]:
        return {"traces": settings.traces.list(limit)}

    @app.get("/trace/{call_id}")
    async def get_trace(call_id: str) -> dict[str, Any]:
        record = settings.traces.get(call_id)
        if record is None:
            raise GatewayError("unknown_call", f"Trace 不存在: {call_id}")
        return record

    return app


def new_call_id() -> str:
    return f"call-{uuid.uuid4().hex}"


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def failed_payload(call_id: str, exc: GatewayError) -> dict[str, Any]:
    """流式例外：首块后失败不再适用 HTTP envelope，事件里仍带统一错误结构。"""
    return {"call_id": call_id, "error": exc.envelope()["error"]}
