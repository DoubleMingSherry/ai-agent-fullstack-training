# 登录会话资料

## 模块入口
教学项目 login_demo 的会话有效性判断入口为 app/session.py，函数名 is_session_valid。对应测试文件是 tests/test_session.py。这是教学资料中的定位记录；使用前必须打开当前源码核对。

## 到期边界规则
本教学项目要求 now == expires_at 时会话无效；有效条件为 now < expires_at。该要求仅适用于 login_demo，不可推广成所有系统的时间边界规则。

## 测试入口
本教学项目约定的测试入口为 python -m pytest tests/test_session.py -q。至少检查到期前、恰好到期、到期后三种情况。本文给出测试入口和预期，不代表本轮已执行命令或测试已经通过。

## 历史修复记录
历史教学案例曾使用 now <= expires_at，导致恰好到期仍返回 True。修复方案是改成 now < expires_at。这里只描述历史问题和方案；当前源码是否采用此方案、当前测试是否通过，须由本轮读取和执行回执确认。
