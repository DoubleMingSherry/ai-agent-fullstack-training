"""FakeProvider — fully scripted offline provider for unit tests.

It never touches the network.  Failure type, chunk count/content and invalid
JSON are all configurable so every acceptance scenario (retry, fallback,
streaming red lines, schema failures) can be scripted deterministically.
"""

from __future__ import annotations

from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Sequence, Union

from gateway.models import TokenUsage
from gateway.providers.base import (
    Provider,
    ProviderContent,
    ProviderDone,
    ProviderError,
    ProviderEvent,
    ProviderRequest,
    ProviderResult,
)

PlanItem = Union[str, ProviderError]  # a str chunk or a scripted failure


def retryable_error(message: str = "retryable fake upstream failure") -> ProviderError:
    return ProviderError(message, retryable=True)


def fatal_error(message: str = "fatal fake upstream failure") -> ProviderError:
    return ProviderError(message, retryable=False)


class FakeProvider(Provider):
    """Scripted provider: per-open queues of failures + stream plan."""

    adapter_name = "fake"

    def __init__(
        self,
        text: str = "Fake answer",
        *,
        input_tokens: int = 10,
        output_tokens: int = 5,
        cache_read: int = 0,
        cache_create: int = 0,
        chunks: Optional[Sequence[str]] = None,
        stream_plan: Optional[Sequence[PlanItem]] = None,
        complete_failures: Sequence[ProviderError] = (),
        stream_failures: Sequence[ProviderError] = (),
    ) -> None:
        self.text = text
        self._usage_kwargs: Dict[str, int] = dict(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_create,
        )
        if stream_plan is not None:
            self._stream_plan: Optional[List[PlanItem]] = list(stream_plan)
        elif chunks is not None:
            self._stream_plan = list(chunks)
        else:
            self._stream_plan = [text] if text else None

        self.complete_failures: Deque[ProviderError] = deque(complete_failures)
        self.stream_failures: Deque[ProviderError] = deque(stream_failures)
        self.complete_calls = 0
        self.stream_opens = 0
        self.last_request: Optional[ProviderRequest] = None

    def _usage(self) -> TokenUsage:
        return TokenUsage(**self._usage_kwargs)

    # ------------------------------------------------------------------
    async def complete(self, request: ProviderRequest) -> ProviderResult:
        self.complete_calls += 1
        self.last_request = request
        if self.complete_failures:
            raise self.complete_failures.popleft()
        return ProviderResult(text=self.text, usage=self._usage())

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        return self._stream(request)

    async def _stream(self, request: ProviderRequest) -> AsyncIterator[Any]:
        self.stream_opens += 1
        self.last_request = request
        if self.stream_failures:
            raise self.stream_failures.popleft()
        for item in self._stream_plan or []:
            if isinstance(item, ProviderError):
                raise item
            yield ProviderContent(delta=item)
        yield ProviderDone(usage=self._usage())
