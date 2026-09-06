"""OpenAI **Responses API** adapter (services ``deepseek-v4-pro``).

Authentication, request body shape and the event-stream format of the
Responses API are encapsulated here and nowhere else.  The SDK client is built
with ``max_retries=0`` — retry policy is owned exclusively by the gateway's
execution layer.  Credentials come from ``gateway.config`` (env-only).

Structured-output layer 1: the Responses API supports a native JSON-schema
constraint (``text.format.json_schema``), so the business schema is passed as a
parameter.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from ..models import TokenUsage
from .base import (
    Provider,
    ProviderContent,
    ProviderDone,
    ProviderError,
    ProviderEvent,
    ProviderRequest,
    ProviderResult,
)


class ResponsesApiAdapter(Provider):
    """Provider implementation over the OpenAI Responses API."""

    adapter_name = "responses_api"

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        if not api_key:
            raise ValueError("ResponsesApiAdapter requires an API key (env RESPONSES_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any = None
        # SDK exception classes live on the instance (populated on first use),
        # so classification below is plain isinstance — no module-attribute hacks.
        self._error_types: Optional[Dict[str, Any]] = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import openai as sdk

            kwargs: Dict[str, Any] = {"api_key": self._api_key, "max_retries": 0}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = sdk.AsyncOpenAI(**kwargs)
            self._error_types = {
                "timeout": sdk.APITimeoutError,
                "connection": sdk.APIConnectionError,
                "status": sdk.APIStatusError,
            }
        return self._client

    # ------------------------------------------------------------------
    def _classify(self, exc: Exception) -> ProviderError:
        """Unified classification.  Retryable whitelist: connection error /
        timeout / upstream 429; everything else is fatal to this attempt."""
        if not self._error_types:
            return ProviderError(f"responses upstream failure: {exc}", retryable=False)
        if isinstance(exc, (self._error_types["timeout"], self._error_types["connection"])):
            return ProviderError(
                f"responses upstream connection failure: {exc}", retryable=True
            )
        if isinstance(exc, self._error_types["status"]):
            status = int(getattr(exc, "status_code", 0) or 0)
            return ProviderError(
                f"responses upstream HTTP {status}: {exc}", retryable=status == 429
            )
        return ProviderError(f"responses upstream failure: {exc}", retryable=False)

    def _build_payload(self, request: ProviderRequest, stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role in ("user", "assistant")
            ],
            "stream": stream,
        }
        if request.system:
            payload["instructions"] = request.system
        if request.json_schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "structured_output",
                    "schema": request.json_schema,
                    "strict": False,
                }
            }
        if request.max_tokens:
            payload["max_output_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if not payload["input"]:
            payload["input"] = [{"role": "user", "content": "Hello"}]
        return payload

    @staticmethod
    def _usage_of(response: Any) -> TokenUsage:
        usage = getattr(response, "usage", None) or {}
        input_details = getattr(usage, "input_tokens_details", None) or {}
        return TokenUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_input_tokens=int(
                getattr(input_details, "cached_tokens", 0) or 0
            ),
        )

    # ------------------------------------------------------------------
    async def complete(self, request: ProviderRequest) -> ProviderResult:
        client = self._ensure_client()
        try:
            response = await client.responses.create(**self._build_payload(request, stream=False))
        except Exception as exc:  # noqa: BLE001 - unified classification
            raise self._classify(exc) from exc
        text = str(getattr(response, "output_text", "") or "")
        return ProviderResult(text=text, usage=self._usage_of(response))

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        return self._stream(request)

    async def _stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        client = self._ensure_client()
        try:
            stream = await client.responses.create(**self._build_payload(request, stream=True))
            completed = False
            async for event in stream:
                event_type = str(getattr(event, "type", ""))
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        yield ProviderContent(delta=str(delta))
                elif event_type == "response.completed":
                    completed = True
                    usage = self._usage_of(getattr(event, "response", None))
                    yield ProviderDone(usage=usage)
                elif event_type == "response.failed":
                    raise ProviderError(
                        "responses upstream reported response.failed", retryable=False
                    )
            if not completed:  # pragma: no cover - defensive
                raise ProviderError("responses stream ended without completion", retryable=True)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
