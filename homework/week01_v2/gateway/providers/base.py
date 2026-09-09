"""Unified Provider protocol — layer 3 boundary.

The gateway consumes ONLY these unified types from an adapter:

* ``ProviderRequest``  — model + system prompt + chat messages + optional
  business JSON schema + generation knobs
* ``ProviderResult``   — ``str`` text + ``TokenUsage`` + ``truncated``
  (non-streaming); ``truncated`` is the normalized length-truncation marker
* ``ProviderEvent``    — streaming: ``ProviderContent`` (content delta) and
  ``ProviderDone`` (final classified usage + ``truncated`` marker)

RED LINES honoured here:
* No vendor SDK object (completion/response/event) ever leaves an adapter;
  everything else in the gateway is decoupled from vendor types.  Stop-reason
  normalization (Responses ``status``/``incomplete_details`` and Messages
  ``stop_reason == "max_tokens"``) happens INSIDE the adapter and only the
  unified boolean ``truncated`` crosses the boundary.
* Adapters map every vendor exception onto a unified ``ProviderError`` whose
  ``retryable`` flag is the execution-layer whitelist (connection error /
  timeout / upstream 429 only).  Everything else is fatal to that attempt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from ..models import ChatMessage, TokenUsage

ProviderRole = Literal["user", "assistant"]


class ProviderRequest(BaseModel):
    model: str
    system: Optional[str] = None                 # rendered prompt template
    messages: List[ChatMessage] = Field(default_factory=list)  # user/assistant only
    json_schema: Optional[Dict[str, Any]] = None  # business JSON Schema (layer 1)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


@dataclass
class ProviderResult:
    text: str
    usage: TokenUsage
    truncated: bool = False  # normalized: stopped by length/context limit


@dataclass
class ProviderContent:
    delta: str


@dataclass
class ProviderDone:
    usage: TokenUsage
    truncated: bool = False  # normalized: stopped by length/context limit


ProviderEvent = Union[ProviderContent, ProviderDone]


class ProviderError(Exception):
    """Unified provider failure with a retry classification."""

    def __init__(self, message: str, *, retryable: bool, kind: str = "upstream") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind
        self.message = message


class Provider(ABC):
    """Protocol implemented by every adapter (and by FakeProvider in tests)."""

    adapter_name: str

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResult:
        """Non-streaming completion."""

    @abstractmethod
    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        """Streaming completion as unified events."""

    def close(self) -> None:  # pragma: no cover - optional cleanup hook
        pass
