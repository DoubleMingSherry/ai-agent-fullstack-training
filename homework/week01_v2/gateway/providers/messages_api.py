"""Anthropic **Messages API** adapter (services ``deepseek-v4-flash``).

Authentication, request body shape and the event-stream format of the Messages
API are encapsulated here and nowhere else.  The SDK client is built with
``max_retries=0`` — retry policy is owned exclusively by the gateway's
execution layer.  Credentials come from ``gateway.config`` (env-only).

Structured-output layer 1 differs per protocol by design: the Messages API has
no native JSON-schema constraint, so the schema is injected into the system
prompt as an instruction (the difference stays inside this adapter).
"""

from __future__ import annotations

import json
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

DEFAULT_MAX_OUTPUT_TOKENS = 4096


class MessagesApiAdapter(Provider):
    """Provider implementation over the Anthropic Messages API."""

    adapter_name = "messages_api"

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        if not api_key:
            raise ValueError("MessagesApiAdapter requires an API key (env MESSAGES_API_KEY)")
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any = None
        # SDK exception classes live on the instance (populated on first use),
        # so classification below is plain isinstance — no module-attribute hacks.
        self._error_types: Optional[Dict[str, Any]] = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic as sdk

            kwargs: Dict[str, Any] = {"api_key": self._api_key, "max_retries": 0}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = sdk.AsyncAnthropic(**kwargs)
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
            return ProviderError(f"messages upstream failure: {exc}", retryable=False)
        if isinstance(exc, (self._error_types["timeout"], self._error_types["connection"])):
            return ProviderError(
                f"messages upstream connection failure: {exc}", retryable=True
            )
        if isinstance(exc, self._error_types["status"]):
            status = int(getattr(exc, "status_code", 0) or 0)
            return ProviderError(
                f"messages upstream HTTP {status}: {exc}", retryable=status == 429
            )
        return ProviderError(f"messages upstream failure: {exc}", retryable=False)

    def _build_payload(self, request: ProviderRequest, stream: bool) -> Dict[str, Any]:
        system = request.system or ""
        if request.json_schema:
            schema_text = (
                "\n\nRespond ONLY with a single JSON object that conforms exactly "
                "to the following JSON Schema. Do not include explanations, fences "
                "or prose outside the JSON object.\n"
                + json.dumps(request.json_schema, ensure_ascii=False)
            )
            system = (system + schema_text).strip()
        payload: Dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
                if m.role in ("user", "assistant")
            ],
            "max_tokens": request.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if not payload["messages"]:
            payload["messages"] = [{"role": "user", "content": "Hello"}]
        return payload

    @staticmethod
    def _usage_of(message_or_usage: Any) -> TokenUsage:
        usage = getattr(message_or_usage, "usage", message_or_usage)
        if usage is None:
            return TokenUsage()
        return TokenUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )

    # ------------------------------------------------------------------
    async def complete(self, request: ProviderRequest) -> ProviderResult:
        client = self._ensure_client()
        try:
            message = await client.messages.create(**self._build_payload(request, stream=False))
        except Exception as exc:  # noqa: BLE001 - unified classification
            raise self._classify(exc) from exc
        blocks = getattr(message, "content", []) or []
        text = "".join(
            str(getattr(block, "text", ""))
            for block in blocks
            if getattr(block, "type", "") == "text"
        )
        # Normalize the Messages stop signal to one unified boolean:
        # stop_reason == "max_tokens" -> truncated (only the boolean leaves).
        truncated = str(getattr(message, "stop_reason", "") or "") == "max_tokens"
        return ProviderResult(text=text, usage=self._usage_of(message), truncated=truncated)

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        return self._stream(request)

    async def _stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        client = self._ensure_client()
        try:
            stream = await client.messages.create(**self._build_payload(request, stream=True))
            usage = TokenUsage()
            stop_reason: Optional[str] = None
            async for event in stream:
                event_type = str(getattr(event, "type", ""))
                if event_type == "message_start":
                    # input tokens + cache write/read classification
                    usage = self._usage_of(getattr(event, "message", None))
                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", "") == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            yield ProviderContent(delta=str(text))
                elif event_type == "message_delta":
                    partial = self._usage_of(getattr(event, "usage", None))
                    if partial.output_tokens:
                        usage.output_tokens = partial.output_tokens
                    stop_reason = getattr(getattr(event, "delta", None), "stop_reason", None) or stop_reason
            yield ProviderDone(
                usage=usage, truncated=str(stop_reason or "") == "max_tokens"
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
