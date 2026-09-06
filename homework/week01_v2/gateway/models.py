"""Pydantic protocols of the gateway (layer 1: interface).

The gateway defines its *own* request protocol — upstream agents submit a
platform logical model name (routing key), a prompt-template reference
(name + version), chat messages and an optional business JSON Schema.

The unified response is ``LLMResponse``; it never exposes any vendor object —
only ``str`` content plus classified ``TokenUsage`` (invariant 3).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# request protocol
# --------------------------------------------------------------------------
MessageRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=200_000)


class PromptRef(BaseModel):
    """Template reference: platform name + version (+ render variables)."""

    name: str = Field(min_length=1, max_length=100)
    version: Optional[int] = None  # None -> newest registered version
    variables: Dict[str, str] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Body of POST /chat and POST /stream."""

    model_config = ConfigDict(populate_by_name=True)

    model: str = Field(min_length=1)
    messages: List[ChatMessage] = Field(default_factory=list, max_length=200)
    prompt: Optional[PromptRef] = None
    #: business JSON Schema; transported over the wire as ``schema``
    json_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    max_tokens: Optional[int] = Field(default=None, ge=1, le=64_000)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)


# --------------------------------------------------------------------------
# token usage / cost / prompt trace (observability vocabulary)
# --------------------------------------------------------------------------
class TokenUsage(BaseModel):
    """Token consumption with provider cache classification when available."""

    input_tokens: int = 0                     # non-cached billed input tokens
    output_tokens: int = 0
    cache_read_input_tokens: int = 0          # cache hits (if provider reports)
    cache_creation_input_tokens: int = 0      # cache writes (if provider reports)

    @property
    def total_input_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


class PromptTrace(BaseModel):
    """Prompt name/version/hash actually used — behaviour replay evidence."""

    name: str
    version: int
    hash: str


# --------------------------------------------------------------------------
# response protocol
# --------------------------------------------------------------------------
class LLMResponse(BaseModel):
    """Unified success payload returned by /chat (and content.done of /stream)."""

    call_id: str
    model: str                     # requested logical model
    model_used: str                # actual serving model (invariant-3 evidence)
    adapter: str                   # actual protocol adapter used
    attempts: int                  # total upstream attempts (retries incl.)
    text: Optional[str] = None     # raw text answer
    structured: Optional[Any] = None  # schema-validated object (layer-2 pass)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float = 0.0              # price(model_used) x tokens
    latency_ms: float = 0.0        # execution start -> response completion
    ttft_ms: Optional[float] = None  # execution start -> first content chunk
    prompt: Optional[PromptTrace] = None
