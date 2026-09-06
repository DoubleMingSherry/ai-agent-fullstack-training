"""Pricing micro-checks: cache-hit tokens are billed at a discount, cache
creation and plain input at full input price (real vendors price cache reads
cheaper — worth an interview discussion)."""

from __future__ import annotations

import pytest

from gateway.models import TokenUsage
from gateway.registry import builtin_registry
from gateway.service import spec_cost


def test_cache_read_tokens_billed_at_discount():
    registry = builtin_registry()
    pro = registry.get("deepseek-v4-pro")       # input $2.0/1M, discount 0.5
    flash = registry.get("deepseek-v4-flash")   # input $0.5/1M, discount 0.1

    read_1m = TokenUsage(cache_read_input_tokens=1_000_000)
    assert spec_cost(pro, read_1m) == pytest.approx(1.0)     # 2.0 × 0.5
    assert spec_cost(flash, read_1m) == pytest.approx(0.05)  # 0.5 × 0.1


def test_plain_input_and_cache_creation_billed_full_price():
    registry = builtin_registry()
    pro = registry.get("deepseek-v4-pro")
    assert spec_cost(pro, TokenUsage(input_tokens=1_000_000)) == pytest.approx(2.0)
    assert spec_cost(pro, TokenUsage(cache_creation_input_tokens=1_000_000)) == pytest.approx(2.0)
