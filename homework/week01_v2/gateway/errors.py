"""Gateway-internal error protocol.

Every HTTP-level failure is returned as a unified envelope::

    {"error": {"code": <str>, "message": <str>, "call_id": <str>}}

Status codes follow the documented categories: request problems 4xx,
rate limiting 429, upstream/gateway failures 5xx.  The error *code* is the
real protocol (``unknown_model``, ``rate_limited``, ...).

Streaming carve-out: once the first chunk of ``/stream`` was already emitted
an HTTP envelope is no longer applicable — the stream ends with a
``response.failed`` event instead (see ``gateway.service``).
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------
# error code -> HTTP status
# --------------------------------------------------------------------------
ERROR_HTTP_STATUS: dict[str, int] = {
    "invalid_request": 400,            # request-body / protocol problem
    "unknown_model": 404,              # not in whitelist
    "unknown_prompt_template": 404,    # unknown template name or version
    "missing_prompt_variable": 400,    # required template variable absent
    "invalid_prompt_variable": 400,    # variable too long / wrong type
    "schema_validation_failed": 422,   # egress validation could not be repaired
    "rate_limited": 429,               # per-model quota exceeded (window)
    "upstream_error": 502,             # provider failed and no fallback configured
    "fallback_exhausted": 502,         # fallback chain exhausted (backup also failed)
    "unknown_trace": 404,              # GET /trace/{call_id} miss
    "internal_error": 500,             # unexpected gateway bug
}


class GatewayError(Exception):
    """A failure with a protocol error code, raised anywhere in the gateway."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: Optional[int] = None,
        retry_after: Optional[float] = None,
        call_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status or ERROR_HTTP_STATUS.get(code, 500)
        self.retry_after = retry_after
        self.call_id = call_id

    def envelope(self, call_id: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "call_id": call_id if call_id is not None else self.call_id,
        }
        return {"error": payload}
