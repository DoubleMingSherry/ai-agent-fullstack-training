"""统一错误协议：错误码本身即协议。

HTTP 状态码按类别映射：请求问题 4xx、限流 429、上游/网关失败 5xx。
HTTP 层错误一律返回 ``{"error": {"code", "message", "call_id"}}``。
"""

from __future__ import annotations

from typing import Any

# 错误码 → HTTP 状态码（类别映射，错误码即协议的一部分）
ERROR_STATUS: dict[str, int] = {
    # 请求问题 → 4xx
    "invalid_request": 400,
    "missing_prompt_variable": 400,
    "invalid_prompt_variable": 400,
    "unknown_model": 404,
    "unknown_prompt_template": 404,
    "unknown_call": 404,
    # 限流 → 429（接口层拒绝，不进入执行层）
    "rate_limited": 429,
    # 上游/网关失败 → 5xx
    "schema_validation_failed": 502,
    "upstream_error": 502,
    "fallback_exhausted": 502,
    "internal_error": 500,
}


def error_envelope(code: str, message: str, call_id: str | None) -> dict[str, Any]:
    """统一错误 envelope：``{error: {code, message, call_id}}``。"""
    return {"error": {"code": code, "message": message, "call_id": call_id}}


class GatewayError(Exception):
    """网关内部统一业务异常，由 FastAPI 异常处理器转换为错误 envelope。

    ``attempts`` / ``model_used`` 由执行层回填，供 Trace 记录失败现场。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        call_id: str | None = None,
        headers: dict[str, str] | None = None,
        attempts: int = 0,
        model_used: str | None = None,
    ) -> None:
        if code not in ERROR_STATUS:
            raise ValueError(f"未注册的错误码: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.call_id = call_id
        self.headers = headers or {}
        self.attempts = attempts  # 执行层回填，供 Trace 记录失败现场
        self.model_used = model_used

    @property
    def status(self) -> int:
        return ERROR_STATUS[self.code]

    def envelope(self) -> dict[str, Any]:
        return error_envelope(self.code, self.message, self.call_id)
