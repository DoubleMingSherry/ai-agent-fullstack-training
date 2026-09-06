"""Model whitelist, dynamic routing and pricing (layer 2: governance).

Only whitelisted platform logical model names are routable.  Each entry
declares its protocol adapter type — the gateway routes on it — plus its
capability set, optional fallback and per-token pricing.  ``validate_model``
guarantees that a fallback only ever happens between capability-equivalent
models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional

from .errors import GatewayError

# --------------------------------------------------------------------------
# capability vocabulary
# --------------------------------------------------------------------------
CAP_TEXT = "text"
CAP_JSON_SCHEMA = "json_schema"  # structured output first-layer guarantee
CAP_STREAM = "stream"

# Both real logical models declare the same capability set, therefore the
# declared fallback (pro -> flash) is capability-equivalent.
_REAL_CAPABILITIES: FrozenSet[str] = frozenset({CAP_TEXT, CAP_JSON_SCHEMA, CAP_STREAM})

# protocol adapter types
ADAPTER_RESPONSES = "responses_api"
ADAPTER_MESSAGES = "messages_api"


@dataclass(frozen=True)
class ModelSpec:
    """One whitelisted logical model."""

    name: str
    adapter_type: str                         # responses_api | messages_api
    capabilities: FrozenSet[str]
    input_price_per_million: float            # per 1M input tokens
    output_price_per_million: float           # per 1M output tokens
    fallback: Optional[str] = None            # capability-equivalent backup
    rate_per_minute: int = 60                 # default quota (config injectable)
    cache_read_discount: float = 0.5          # cache-hit tokens billed at this
                                              # fraction of the input price


class ModelRegistry:
    """Built-in whitelist with pricing, plus capability-aware validation."""

    def __init__(self, specs: List[ModelSpec]) -> None:
        self._specs: Dict[str, ModelSpec] = {s.name: s for s in specs}
        self._validate_graph()

    def _validate_graph(self) -> None:
        for spec in self._specs.values():
            if spec.fallback and spec.fallback not in self._specs:
                raise GatewayError(
                    "internal_error",
                    f"fallback of {spec.name!r} points to unknown model {spec.fallback!r}",
                )
            if spec.fallback and spec.fallback == spec.name:
                raise GatewayError("internal_error", f"{spec.name} cannot fall back to itself")

    def get(self, model: str) -> ModelSpec:
        spec = self._specs.get(model)
        if spec is None:
            raise GatewayError("unknown_model", f"model {model!r} is not in the whitelist")
        return spec

    def capability_ok(self, model: str, needed: FrozenSet[str]) -> bool:
        return needed <= self._specs[model].capabilities

    def fallback_chain(self, model: str) -> List[str]:
        """Ordered candidate chain starting with ``model`` itself."""
        chain: List[str] = []
        seen: set[str] = set()
        current: Optional[str] = model
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self._specs[current].fallback
        return chain

    def ensure_fallback_capability(self, primary: ModelSpec, backup_name: str) -> None:
        """Guarantee a fallback is only used when capability-equivalent."""
        backup = self._specs[backup_name]
        if primary.capabilities != backup.capabilities:
            raise GatewayError(
                "internal_error",
                f"fallback {backup_name!r} is not capability-equivalent to {primary.name!r}",
            )

    def cost(self, model: str, usage: Any) -> float:
        """Cost = price(model) x tokens; cache reads billed at the discount."""
        spec = self._specs[model]
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        create = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        billed_input = int(getattr(usage, "input_tokens", 0) or 0) + create
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return (
            billed_input / 1_000_000.0 * spec.input_price_per_million
            + read / 1_000_000.0 * spec.input_price_per_million * spec.cache_read_discount
            + output_tokens / 1_000_000.0 * spec.output_price_per_million
        )

    def specs(self) -> List[ModelSpec]:
        return list(self._specs.values())


def builtin_registry() -> ModelRegistry:
    """The two real models, same declared capability set (fallback safe)."""
    return ModelRegistry(
        [
            ModelSpec(
                name="deepseek-v4-pro",
                adapter_type=ADAPTER_RESPONSES,
                capabilities=_REAL_CAPABILITIES,
                input_price_per_million=2.0,
                output_price_per_million=8.0,
                fallback="deepseek-v4-flash",
                rate_per_minute=60,
            ),
            ModelSpec(
                name="deepseek-v4-flash",
                adapter_type=ADAPTER_MESSAGES,
                capabilities=_REAL_CAPABILITIES,
                input_price_per_million=0.5,
                output_price_per_million=1.5,
                fallback=None,
                rate_per_minute=120,
                cache_read_discount=0.1,  # Anthropic-style cheap cache reads
            ),
        ]
    )
