"""Provider adapters live here; each one encapsulates ONE vendor protocol.

RED LINE: vendor SDK objects never leave this package — the rest of the
gateway only consumes ``str`` + ``TokenUsage`` and the unified stream events
defined in ``gateway.providers.base``.
"""

from .base import (  # noqa: F401
    Provider,
    ProviderContent,
    ProviderDone,
    ProviderError,
    ProviderEvent,
    ProviderRequest,
    ProviderResult,
)

__all__ = [
    "Provider",
    "ProviderContent",
    "ProviderDone",
    "ProviderError",
    "ProviderEvent",
    "ProviderRequest",
    "ProviderResult",
]
