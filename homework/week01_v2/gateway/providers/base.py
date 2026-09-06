"""Unified Provider protocol — layer 3 boundary.

The gateway consumes ONLY these unified types from an adapter:

* ``ProviderRequest``  — model + system prompt + chat messages + optional
  business JSON schema + generation knobs
* ``ProviderResult``   — ``str`` text + ``TokenUsage`` (non-streaming)
* ``ProviderEvent``    — streaming: ``ProviderContent`` (content delta) and
  ``ProviderDone`` (final classified usage)

RED LINES honoured here:
* No vendor SDK object (completion/response/event) ever leaves an adapter;
  everything else in the gateway is decoupled from vendor types.
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


@dataclass
class ProviderContent:
    delta: str


@dataclass
class ProviderDone:
    usage: TokenUsage


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
