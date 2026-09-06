"""Per-model in-memory rate limiting (layer 2: governance).

Rate-limit key == logical model name (decided by governance, independent of
the actually served ``model_used``).  ``X-Caller-Id`` is a *trace* label only,
never a rate-limit key.  Requests beyond the window quota are refused 429 with
``Retry-After`` and never enter the execution layer.

A limiter 429 is an interface-layer refusal: it counts no ``attempts`` and
triggers no retry (upstream 429s belong to the execution-layer retryable
whitelist instead).
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

WINDOW_SECONDS = 60.0


class _ModelLimiter:
    """Fixed 60s window counter for a single logical model."""

    def __init__(self, quota_per_minute: int) -> None:
        if quota_per_minute < 1:
            raise ValueError("quota_per_minute must be >= 1")
        self.quota = quota_per_minute
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._count = 0

    def acquire(self, now: Optional[float] = None) -> Tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if now - self._window_start >= WINDOW_SECONDS:
                self._window_start = now
                self._count = 0
            if self._count < self.quota:
                self._count += 1
                return True, 0.0
            retry_after = max(0.0, self._window_start + WINDOW_SECONDS - now)
            return False, retry_after


class RateLimiter:
    """One independent limiter per logical model."""

    def __init__(self, quotas: Dict[str, int]) -> None:
        self._limiters: Dict[str, _ModelLimiter] = {
            model: _ModelLimiter(q) for model, q in quotas.items()
        }
        self._lock = threading.Lock()

    def acquire(self, model: str) -> Tuple[bool, float]:
        with self._lock:
            limiter = self._limiters.get(model)
            if limiter is None:
                limiter = _ModelLimiter(1 << 30)  # unknown models already blocked earlier
                self._limiters[model] = limiter
        return limiter.acquire()
