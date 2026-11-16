---
service: login_demo
topic: requirement
owner: 认证组
recorded: 2026-10-06
---

# 需求讨论 2026-10-06：增加退出登录功能

## 背景

用户希望主动退出，而不是等 30 分钟自然到期。

## 需要改的地方

- `app/auth.py`：把 `logout()` 从 `NotImplementedError` 改成真实实现，删除 `_TOKEN_STORE` 里的记录。
- `app/session.py`：新增 `invalidate(token)` 之类的辅助函数，或直接在 auth 层删除记录，二选一，先按第一种做。
- `tests/test_session.py`：新增 `test_logout_invalidates`。

## 验收标准

- 退出后再用同一个令牌调用 `check()`，返回 False。
- `python -m pytest tests/test_session.py -q` 全绿。
- 不修改既有测试文件的期望值。

## 未决问题

- 退出登录是否需要同时清理服务端缓存，待运维确认。
