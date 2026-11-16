---
type: Fact
title: Token 格式与载荷
description: login_demo 的 access token 为 demo-token.<base64url(JSON)> 结构,载荷仅含 userId 与 role,无签名。
tags: [login_demo, token, 格式]
status: stable
generated: { by: human:wilson, at: 2026-10-07T00:00:00Z }
verified: { by: human:wilson, at: 2026-10-07T00:00:00Z }
---

# 格式

```
demo-token.<base64url(JSON.stringify({ userId, role }))>
```

签发与解析均在 `login_demo/src/utils/token.ts`。

# 载荷

来源 `login_demo/src/types/user.ts`:

```ts
export interface AuthTokenPayload {
  userId: string;
  role: UserRecord["role"];   // "admin" | "member"
}
```

仅两个字段,无 `iat` / `exp`(见 [无 token 到期规则](/sessions/expiry-rule-absent.md))。

# 签发

`issueAccessToken(payload)`:

```ts
const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
return `demo-token.${encoded}`;
```

# 解析

`parseAccessToken(token)` 三个判断,任一失败返回 `null`:

1. `token.startsWith("demo-token.")`;
2. `Buffer.from(encoded, "base64url").toString("utf8")` 不抛错;
3. `JSON.parse(json)` 不抛错。

成功则 `return JSON.parse(json) as AuthTokenPayload`。**注意:解析结果未做字段结构校验**,只要 JSON 可解析即被当作合法载荷。

# 已执行验证的实测值

在进程内 require `dist/` 模块直接调用(绕过 HTTP,可在本环境执行):

| 输入 | 输出 |
|---|---|
| `issueAccessToken({ userId: "u_1001", role: "member" })` | `demo-token.eyJ1c2VySWQiOiJ1XzEwMDEiLCJyb2xlIjoibWVtYmVyIn0` |
| `parseAccessToken(上述 token)` | `{ userId: "u_1001", role: "member" }` |
| `parseAccessToken("nonsense")` | `null` |
| `parseAccessToken("demo-token.!!!not-base64!!!")` | `null` |

# 待验证

- 中间件对畸形但可解析的载荷(如缺 `userId` 的对象)的处理,未做端到端验证。
- 该 token 与标准 JWT 无关,不可套用 JWT 校验/过期语义。
