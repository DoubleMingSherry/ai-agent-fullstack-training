---
type: Fact
title: 会话模块位置与调用链
description: login_demo 的会话实现由登录签发 token 与认证中间件校验 token 两段组成,无独立 session 存储层。
tags: [login_demo, session, auth, 源码定位]
status: stable
generated: { by: human:wilson, at: 2026-10-07T00:00:00Z }
verified: { by: human:wilson, at: 2026-10-07T00:00:00Z }
---

# 结论

登录会话逻辑位于 `login_demo/` 子目录,**不在仓库根目录**。会话没有独立存储层,而是由「登录时签发无状态 token」+「中间件解析 token」两段组成。

# 文件定位

| 职责 | 文件 | 关键符号 |
|---|---|---|
| 运行入口 | `login_demo/src/server.ts` | `app.listen(3001)` |
| 应用装配 | `login_demo/src/app.ts` | `app.use("/api", loginRouter)`、`profileRouter`、`GET /health` |
| 登录路由 | `login_demo/src/routes/login.ts` | `loginRouter.post("/login", ...)` |
| 会话业务逻辑 | `login_demo/src/services/auth-service.ts` | `AuthService.login(email, password)` |
| 用户查询 | `login_demo/src/repositories/user-repository.ts` | `findByEmail(email)` |
| 模拟数据表 | `login_demo/src/db/database.ts` | 内存数组 `usersTable`、`queryOne(table, where)` |
| 密码校验 | `login_demo/src/utils/password.ts` | `verifyPassword(plain, hash)` |
| **Token 签发/解析** | `login_demo/src/utils/token.ts` | `issueAccessToken` / `parseAccessToken` |
| **认证中间件** | `login_demo/src/middleware/auth-middleware.ts` | `authMiddleware` |
| 受保护路由 | `login_demo/src/routes/profile.ts` | `GET /api/profile` |

会话核心是两个加粗文件:签发在 `utils/token.ts`,校验在 `middleware/auth-middleware.ts`。

# 登录链路

```
POST /api/login
  -> routes/login.ts        读取 email/password,缺字段直接 400
  -> services/auth-service.ts  AuthService.login
  -> repositories/user-repository.ts  findByEmail
  -> db/database.ts          queryOne("users", { email })
  -> utils/password.ts       verifyPassword
  -> utils/token.ts          issueAccessToken({ userId, role })
  -> 200 { accessToken, user }
```

失败分支:`User not found` 与 `Invalid password` 均由 Service 抛出,路由层统一转 `401`。

# 鉴权链路

```
GET /api/profile
  -> middleware/auth-middleware.ts  读取 Authorization: Bearer <token>
  -> utils/token.ts                 parseAccessToken
  -> response.locals.currentUser = payload
  -> routes/profile.ts              200 { message, currentUser }
```

中间件三种结果:请求头缺失或非 `Bearer ` 前缀 → `401 Missing bearer token`;解析返回 null → `401 Invalid token`;成功则写入 `currentUser` 并 `next()`。

# 实现性事实

- 无服务端 session 存储、无 cookie、无 refresh token。
- 无签名/HMAC 校验,token 是纯 base64url 透明载荷。
- `AuthTokenPayload` 仅含 `userId`、`role`(见 `src/types/user.ts`)。
- 密码比对为教学实现:`passwordHash === "hash:" + 明文`,非真实哈希。
- 内置两条用户记录:`demo@example.com`(member)、`admin@example.com`(admin)。**密码为 demo 明文占位值,此处不复录。**

# 证据

- 源码直接阅读:`src/` 全部 11 个 TypeScript 文件。
- 交叉印证:`login_demo/README.md`、`login_demo/artifacts/login-flow.md` 与本结论一致。
- 已执行验证:`dist/` 编译产物中 `queryOne`、`verifyPassword`、`parseAccessToken` 行为与源码一致(见 [测试入口与验证手段](/testing/test-entry.md))。

# 待验证

- 鉴权链路的端到端 HTTP 行为未取得执行证据(本环境禁止监听端口),仅从源码推断。详见 [环境限制与失败记录](/testing/environment-limits.md)。
