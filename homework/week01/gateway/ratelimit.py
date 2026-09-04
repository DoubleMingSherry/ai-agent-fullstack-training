"""治理层：按调用方的内存限流（单实例，固定窗口计数器）。

调用方以请求头 ``X-Caller-Id`` 标识（缺省 default）。
窗口内超过配额直接 429 + Retry-After，请求不进入执行层。
注意边界：这里的 429 是接口层拒绝，不计 attempts、不触发重试；
上游返回的 429 属于执行层 is_retryable 白名单，两者不混用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    quota: int = 60  # 每窗口允许的调用次数
    window_seconds: float = 60.0


class RateLimiter:
    def __init__(self, config: RateLimitConfig) -> None:
        self.config = config
        self._counters: dict[str, tuple[float, int]] = {}  # caller → (窗口起点, 计数)

    def check(self, caller_id: str) -> tuple[bool, float]:
        """消耗一次配额；返回 (是否放行, 被拒时的 Retry-After 秒数)。"""
        now = time.monotonic()
        window_start, count = self._counters.get(caller_id, (now, 0))
        if now - window_start >= self.config.window_seconds:
            window_start, count = now, 0
        if count >= self.config.quota:
            retry_after = self.config.window_seconds - (now - window_start)
            self._counters[caller_id] = (window_start, count)
            return False, max(retry_after, 0.0)
        self._counters[caller_id] = (window_start, count + 1)
        return True, 0.0
