"""CallTrace storage — layer 5 observability.

Every call (success and failure) is recorded with: call_id, caller_id,
requested model, model_used, adapter, attempts, status, error code, prompt
name/version/hash, classified token usage, cost, latency_ms and ttft_ms.
``model_used`` is the model that actually served the request — the only
observable evidence of invariant 3.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class CallTrace:
    call_id: str
    caller_id: str
    model: str                          # requested logical model
    model_used: Optional[str] = None    # actually served model (primary/backup)
    adapter: Optional[str] = None       # protocol adapter actually used
    attempts: int = 0                   # total upstream attempts (no limiter refusals)
    status: str = "pending"             # success | error | aborted
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    prompt_name: Optional[str] = None
    prompt_version: Optional[int] = None
    prompt_hash: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)  # token classification
    cost: float = 0.0
    latency_ms: Optional[float] = None
    ttft_ms: Optional[float] = None
    started_at_ms: float = field(default_factory=_now_ms)
    finished_at_ms: Optional[float] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "caller_id": self.caller_id,
            "model": self.model,
            "model_used": self.model_used,
            "adapter": self.adapter,
            "attempts": self.attempts,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "prompt": (
                {
                    "name": self.prompt_name,
                    "version": self.prompt_version,
                    "hash": self.prompt_hash,
                }
                if self.prompt_name is not None
                else None
            ),
            "usage": dict(self.usage),
            "cost": self.cost,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
        }


class TraceStore:
    """Thread-safe in-memory trace store (single instance deployment)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, CallTrace] = {}
        self._order: List[str] = []

    def new(self, call_id: str, caller_id: str, model: str) -> CallTrace:
        trace = CallTrace(call_id=call_id, caller_id=caller_id, model=model)
        with self._lock:
            self._records[call_id] = trace
            self._order.append(call_id)
        return trace

    def get(self, call_id: str) -> Optional[CallTrace]:
        with self._lock:
            return self._records.get(call_id)

    def list_traces(self, caller_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            ordered = [self._records[cid] for cid in reversed(self._order)]
        if caller_id is not None:
            ordered = [t for t in ordered if t.caller_id == caller_id]
        return [t.to_record() for t in ordered[: max(1, min(limit, 1000))]]
