---
service: login_demo
topic: troubleshooting
owner: 认证组
updated: 2026-09-25
---

# 常见问题（FAQ）

## 用户反馈"登录状态老是掉"

1. 先确认会话有效期配置是否被改过，默认 30 分钟。
2. 再看是不是恰好卡在到期时刻被判定为无效，检查边界用例。
3. 最后确认前端是否在响应里正确保存了新的令牌。

## 能否把有效期改长一点

- 可以，但需要产品确认。修改前先读 `docs/session-rules.md`，不要直接改代码常量。

## 忘记测试命令怎么办

- 见 `docs/testing-guide.md`，入口是 `python -m pytest tests/test_session.py -q`。
