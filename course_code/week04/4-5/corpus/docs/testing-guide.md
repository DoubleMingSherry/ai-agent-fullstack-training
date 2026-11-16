---
service: login_demo
topic: testing
owner: 认证组
updated: 2026-09-20
---

# login_demo 测试指南

## 测试入口

- 统一入口：`python -m pytest tests/test_session.py -q`
- 不要直接敲 `pytest tests/test_session.py`，项目的 CI 只认 `python -m pytest` 形式。
- 运行前先激活虚拟环境：`source .venv/bin/activate`。

## 关键用例

- `test_expire_boundary`：覆盖 `now == expires_at`，期望返回 `False`。
- `test_refresh_extends_ttl`：覆盖续期后到期时间被推后 30 分钟。
- `test_logout_invalidates`：退出登录后，同一个会话令牌应判定为无效。

## 提交前检查

- 单元测试必须全绿。
- 不要为了通过测试去修改测试文件，测试文件由维护者负责。
- 新增功能要补一条对应用例，并在提交说明里写清用例名。
