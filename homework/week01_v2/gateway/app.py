"""FastAPI entry — layer 1 interface.

Endpoints:

* ``POST /chat``   — plain (non-streaming) unified call, returns LLMResponse
* ``POST /stream`` — streaming call, returns ``text/event-stream`` whose frames
  carry typed events (``content.delta`` / ``content.done`` / ``response.failed``)
* ``GET  /trace``  — trace listing; ``GET /trace/{call_id}`` — trace detail

Every HTTP-level error is a unified envelope ``{"error": {code, message, call_id}}``
with status by category (4xx request / 429 limited / 5xx upstream).  Streaming
exception: once the first chunk was emitted a failure is delivered as a
``response.failed`` event, not an HTTP envelope.

The factory accepts injected providers, so offline tests mount FakeProviders
via ``httpx.ASGITransport``; ``create_real_app`` wires the real protocol
adapters whose credentials come exclusively from environment variables.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import messages_credentials, responses_credentials
from .errors import ERROR_HTTP_STATUS, GatewayError
from .models import ChatRequest, LLMResponse
from .providers.base import Provider
from .providers.messages_api import MessagesApiAdapter
from .providers.responses_api import ResponsesApiAdapter
from .ratelimit import RateLimiter
from .registry import ModelRegistry, builtin_registry
from .service import DEFAULT_MAX_INPUT_TOKENS, GatewayService
from .templates import TemplateRegistry, builtin_templates
from .trace import TraceStore

DEFAULT_CALLER = "default"


def _quotas_from_registry(
    registry: ModelRegistry, rate_limits: Optional[Mapping[str, int]]
) -> Dict[str, int]:
    quotas = {spec.name: spec.rate_per_minute for spec in registry.specs()}
    if rate_limits:
        quotas.update(dict(rate_limits))
    return quotas


def create_app(
    providers: Mapping[str, Provider],
    *,
    registry: Optional[ModelRegistry] = None,
    templates: Optional[TemplateRegistry] = None,
    rate_limits: Optional[Mapping[str, int]] = None,
    backoff_base: float = 1.0,
    max_retries: int = 3,
    max_input_tokens: Optional[int] = None,  # 入站上下文预算（字符估算），None=默认
    trace_store: Optional[TraceStore] = None,
    sleep: Optional[Any] = None,
) -> FastAPI:
    """Build the gateway with injected providers (tests inject FakeProvider)."""
    registry = registry or builtin_registry()
    templates = templates or builtin_templates()
    trace_store = trace_store or TraceStore()
    service = GatewayService(
        registry=registry,
        templates=templates,
        providers=providers,
        trace_store=trace_store,
        rate_limiter=RateLimiter(_quotas_from_registry(registry, rate_limits)),
        backoff_base=backoff_base,
        max_retries=max_retries,
        max_input_tokens=max_input_tokens or DEFAULT_MAX_INPUT_TOKENS,
        sleep=sleep,
    )

    app = FastAPI(title="Mini LLM Gateway v2", version=__version__)

    # ------------------------------------------------------------------
    # unified error envelope
    # ------------------------------------------------------------------
    def _json_error(status: int, code: str, message: str, call_id: Optional[str] = None,
                    retry_after: Optional[float] = None) -> JSONResponse:
        payload: Dict[str, Any] = {"code": code, "message": message, "call_id": call_id}
        headers: Dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = str(int(retry_after) + (1 if retry_after > int(retry_after) else 0))
        body = {"error": {k: v for k, v in payload.items() if v is not None}}
        return JSONResponse(status_code=status, content=body, headers=headers or None)

    @app.exception_handler(GatewayError)
    async def _gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        call_id = exc.call_id or getattr(request.state, "call_id", None)
        return _json_error(exc.http_status, exc.code, exc.message, call_id, exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", [])) or "body"
        message = f"invalid request at {where}: {first.get('msg', 'validation error')}"
        return _json_error(400, "invalid_request", message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            422: "invalid_request",
            429: "rate_limited",
        }.get(exc.status_code, f"http_{exc.status_code}")
        return _json_error(exc.status_code, code, str(exc.detail))

    # ------------------------------------------------------------------
    # /chat — plain call
    # ------------------------------------------------------------------
    @app.post("/chat", response_model=LLMResponse)
    async def chat(request: ChatRequest, incoming: Request) -> Any:
        caller_id = incoming.headers.get("X-Caller-Id", DEFAULT_CALLER)
        ctx = service.begin(request, caller_id)
        incoming.state.call_id = ctx.call_id
        response = await service.execute_text(ctx)
        return JSONResponse(
            content=json.loads(response.model_dump_json()),
            headers={"X-Call-Id": ctx.call_id},
        )

    # ------------------------------------------------------------------
    # /stream — streaming call
    # ------------------------------------------------------------------
    @app.post("/stream")
    async def stream(request: ChatRequest, incoming: Request) -> StreamingResponse:
        caller_id = incoming.headers.get("X-Caller-Id", DEFAULT_CALLER)
        # governance (model / template / rate limit) answers with a normal
        # HTTP envelope because the stream has not started yet
        ctx = service.begin(request, caller_id)
        incoming.state.call_id = ctx.call_id

        async def _frames() -> AsyncIterator[str]:
            # 课程口径 SSE 线缆格式：event: <name> 行 + data: <json> 行。
            # 具名事件可被浏览器 EventSource 的 addEventListener 直接监听，
            # data-only 会把所有事件都落到 message 监听器上（故不用）。
            async for event in service.stream_events(ctx):
                yield sse_frame(event["event"], event["data"])

        return StreamingResponse(
            _frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Call-Id": ctx.call_id,
            },
        )

    # ------------------------------------------------------------------
    # /trace — observability queries
    # ------------------------------------------------------------------
    @app.get("/trace")
    async def list_traces(caller_id: Optional[str] = None, limit: int = 50) -> Any:
        return {"traces": trace_store.list_traces(caller_id=caller_id, limit=limit)}

    @app.get("/trace/{call_id}")
    async def trace_detail(call_id: str) -> Any:
        record = trace_store.get(call_id)
        if record is None:
            raise GatewayError("unknown_trace", f"no trace found for call {call_id!r}")
        return {"trace": record.to_record()}

    @app.get("/")
    async def health() -> Any:
        return {"service": "mini-llm-gateway-v2", "version": __version__}

    return app


def sse_frame(event: str, data: Any) -> str:
    """One SSE frame in the course wire format: ``event: {name}`` + ``data: {json}``."""
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def create_real_app() -> FastAPI:
    """Wire the real protocol adapters; credentials come from env vars only."""
    responses_key, responses_base = responses_credentials()
    messages_key, messages_base = messages_credentials()
    if not responses_key and not messages_key:
        raise RuntimeError(
            "Missing provider credentials.\n"
            "Set the environment variables before starting the gateway:\n"
            "  RESPONSES_API_KEY (or OPENAI_API_KEY)  + RESPONSES_BASE_URL (optional)\n"
            "  MESSAGES_API_KEY  (or ANTHROPIC_API_KEY) + MESSAGES_BASE_URL (optional)\n"
            "At least one protocol key is required."
        )
    providers: Dict[str, Provider] = {}
    if responses_key:
        providers["responses_api"] = ResponsesApiAdapter(responses_key, responses_base)
    if messages_key:
        providers["messages_api"] = MessagesApiAdapter(messages_key, messages_base)
    return create_app(providers)
