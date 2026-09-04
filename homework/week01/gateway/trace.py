"""可观测层：CallTrace 记录与查询。

每次调用（成功和失败，含被限流的请求）都记录一条：
call_id、model_used（主/备）、attempts、status、错误码、
prompt name+version+hash、输入/输出 Token、Cost、Latency（请求发出到响应完成）。
model_used 记录实际服务请求的模型，是不变量 3 唯一可观测的证据。
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TraceStore:
    """内存 Trace 存储（单实例），按写入顺序保留，支持 call_id 精确查询。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        record.setdefault("created_at", time.time())
        with self._lock:
            stored = dict(record)
            self._records.append(stored)
            self._by_id[stored["call_id"]] = stored
        return dict(stored)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._records[-limit:]]

    def get(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._by_id.get(call_id)
            return dict(record) if record else None


def new_trace(
    call_id: str,
    caller_id: str,
    requested_model: str,
    prompt: dict[str, str] | None = None,
) -> dict[str, Any]:
    """建立一条 Trace 骨架，各层执行过程中逐步回填。"""
    return {
        "call_id": call_id,
        "caller_id": caller_id,
        "requested_model": requested_model,
        "model_used": None,
        "attempts": 0,
        "status": "failed",  # 成功路径结束时改为 ok
        "error_code": None,
        "prompt": prompt,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "latency_ms": 0.0,
    }
