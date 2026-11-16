"""登录会话的判定实现。

本模块只负责"令牌是否仍然有效"的判断，不负责签发和存储。
"""

SESSION_TTL_SECONDS = 30 * 60  # 登录会话有效期，单位秒


def is_session_valid(now, expires_at):
    """判断会话在 now 时刻是否有效。

    参数都是 Unix 时间戳（秒）。
    到期时刻本身视为已过期。
    """
    return now < expires_at


def refresh_expiry(now):
    """返回续期后的新到期时间。"""
    return now + SESSION_TTL_SECONDS
