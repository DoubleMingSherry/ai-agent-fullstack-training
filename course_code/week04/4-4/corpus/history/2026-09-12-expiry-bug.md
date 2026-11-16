---
service: login_demo
topic: incident
owner: 认证组
recorded: 2026-09-12
---

# 故障记录 2026-09-12：到期那一刻仍然可以访问

## 现象

用户报告：令牌显示还剩 0 秒，接口仍然返回 200。

## 排查

- 打开 `app/session.py`，发现判断写的是 `now <= expires_at`。
- 用边界时刻构造请求，`now == expires_at` 返回 True，确认复现。

## 修复

- 把比较运算符从 `<=` 改成 `<`。
- 补一条边界用例，覆盖 `now == expires_at`，期望 False。
- 运行 `python -m pytest tests/test_session.py -q`，用例通过。

## 结论

到期时刻必须判定为无效。这条结论对登录会话长期有效。
