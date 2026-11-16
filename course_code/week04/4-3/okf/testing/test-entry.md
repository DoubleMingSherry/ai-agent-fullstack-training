---
type: Fact
title: 测试入口与验证手段
description: login_demo 无任何测试框架或测试文件,可用的验证手段是 tsc 构建与进程内直调模块。
tags: [login_demo, 测试, 验证记录]
status: stable
generated: { by: human:wilson, at: 2026-10-07T00:00:00Z }
verified: { by: human:wilson, at: 2026-10-07T00:00:00Z }
---

# 结论

**本项目没有任何测试入口。** 无测试文件、无测试目录、无测试框架、无 `test` script。

# 数据

```
find login_demo -path ./node_modules -prune -o \
  \( -name "*.test.*" -o -name "*.spec.*" -o -name "jest.config*" \
     -o -name "vitest.config*" -o -name ".mocharc*" \) -print
→ 无输出

find login_demo -type d \( -name test -o -name tests -o -name __tests__ \)
→ 无输出
```

`package.json` scripts 仅三项:

| script | 命令 | 作用 |
|---|---|---|
| `dev` | `ts-node-dev --respawn --transpile-only src/server.ts` | 开发热重载 |
| `build` | `tsc -p tsconfig.json` | 编译到 `dist/` |
| `start` | `node dist/server.js` | 运行编译产物 |

`devDependencies` 仅 `@types/express`、`ts-node-dev`、`typescript`;无 jest / vitest / mocha / ava / tap。`node_modules/.bin/` 内亦无测试运行器。

# 可用验证手段(按可靠性排序)

## 1. 类型检查 —— `npm run build`

由 `tsconfig.json` 的 `strict: true` 提供类型级保证。这是本项目唯一的自动化质量门槛。

## 2. 进程内直调模块 —— 推荐,本环境可执行

绕过 HTTP 层,直接 require `dist/` 下的纯逻辑模块。无需监听端口,因此在本环境可用。

已执行并取得的实测结果:

| 检查项 | 结果 |
|---|---|
| `issueAccessToken` → `parseAccessToken` 往返 | 载荷正确还原为 `{ userId: "u_1001", role: "member" }` |
| `parseAccessToken("nonsense")` | `null` |
| `parseAccessToken("demo-token.!!!not-base64!!!")` | `null` |
| `verifyPassword(<正确明文>, <对应hash>)` | `true` |
| `verifyPassword(<错误明文>, <对应hash>)` | `false` |

（明文与 hash 值按不记录凭证的纪律隐去，测试时以 `src/db/database.ts` 中的 demo 值为准。）
| `queryOne("users", { email: "demo@example.com" })` | 命中记录 |
| `queryOne("users", { email: "nobody@example.com" })` | `undefined` |

探针脚本置于 `/tmp` 外部,**未向项目写入任何文件,未修改业务源码**。

## 3. 手动冒烟 —— 本环境不可用

设计路径:`npm run dev` 后 curl 三个端点。

| 端点 | 预期 | 验证状态 |
|---|---|---|
| `GET /health` | `200 { ok: true }` | **待验证**(无法监听端口) |
| `POST /api/login` 正确凭证 | `200 { accessToken, user }` | **待验证** |
| `POST /api/login` 错误密码 | `401 Invalid password` | **待验证** |
| `POST /api/login` 缺字段 | `400 email and password are required` | **待验证** |
| `POST /api/login` 不存在用户 | `401 User not found` | **待验证** |
| `GET /api/profile` 无 token | `401 Missing bearer token` | **待验证** |
| `GET /api/profile` 合法 token | `200 { message, currentUser }` | **待验证** |

上表 HTTP 状态均为**从源码推断的预期值**,本环境未能取得执行证据,一律标为待验证。

# 建议的最小测试入口(尚未落地)

若需补齐自动化测试,建议在 `package.json` 增加 `test` script 并覆盖:

1. `verifyPassword` 正确/错误/空哈希三条分支。
2. `parseAccessToken` 前缀缺失、body 非法、正常三类输入。
3. `authService.login` 的用户不存在、密码错误、成功三条路径。
4. `authMiddleware` 的无 header、非 Bearer、非法 token、合法 token 四种情况。

以上为建议,**当前不存在对应代码**。

# 相关

- 具体的失败记录见 [环境限制与失败记录](/testing/environment-limits.md)。
