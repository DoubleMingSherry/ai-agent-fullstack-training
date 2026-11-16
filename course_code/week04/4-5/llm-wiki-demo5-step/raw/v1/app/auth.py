"""登录与退出的入口。

本模块负责令牌的生命周期：签发、校验、续期、失效。
退出登录功能尚未实现，见 logout() 的 TODO。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .session import SESSION_TTL_SECONDS, is_session_valid, refresh_expiry

_TOKEN_STORE: dict[str, dict] = {}
_SECRET = b"login-demo-secret"


def _sign(token: str) -> str:
    """给令牌做一次 HMAC，防止客户端伪造。"""
    return hmac.new(_SECRET, token.encode(), hashlib.sha256).hexdigest()[:16]


def issue_token(user_id: str, now: int | None = None) -> str:
    """签发一个新的会话令牌，并把到期时间写进存储。"""
    now = int(now if now is not None else time.time())
    raw = f"{user_id}.{now}.{secrets.token_hex(8)}"
    token = f"{raw}.{_sign(raw)}"
    _TOKEN_STORE[token] = {
        "user_id": user_id,
        "issued_at": now,
        "expires_at": refresh_expiry(now),
    }
    return token


def verify_token(token: str) -> bool:
    """校验签名，签名不对直接判为无效。"""
    if token.count(".") != 3:
        return False
    raw, signature = token.rsplit(".", 1)
    return hmac.compare_digest(_sign(raw), signature)


def check(token: str, now: int | None = None) -> bool:
    """按令牌查询会话是否有效。"""
    if not verify_token(token):
        return False
    record = _TOKEN_STORE.get(token)
    if record is None:
        return False
    now = int(now if now is not None else time.time())
    return is_session_valid(now, record["expires_at"])


def expire_at(token: str) -> int | None:
    """返回令牌的到期时间戳，令牌不存在时返回 None。"""
    record = _TOKEN_STORE.get(token)
    return record["expires_at"] if record else None


def touch(token: str, now: int | None = None) -> int | None:
    """用户有请求时续期，返回新的到期时间。"""
    record = _TOKEN_STORE.get(token)
    if record is None:
        return None
    now = int(now if now is not None else time.time())
    if not is_session_valid(now, record["expires_at"]):
        return None
    record["expires_at"] = refresh_expiry(now)
    return record["expires_at"]


def purge_expired(now: int | None = None) -> int:
    """清理已过期的记录，返回清理条数。"""
    now = int(now if now is not None else time.time())
    dead = [t for t, r in _TOKEN_STORE.items() if not is_session_valid(now, r["expires_at"])]
    for token in dead:
        _TOKEN_STORE.pop(token, None)
    return len(dead)


def logout(token: str) -> None:
    """退出登录：让令牌立即失效。

    TODO: 尚未实现。需要删除 _TOKEN_STORE 中的记录，
    并按 docs/testing-guide.md 补一条 test_logout_invalidates 用例。
    """
    raise NotImplementedError("logout is not implemented yet")
