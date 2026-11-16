---
type: Fact
title: 环境限制与失败记录
description: 本次取证环境中 ts-node 依赖损坏且端口监听被沙箱拒绝,导致端到端 HTTP 测试无法进行。
tags: [login_demo, 环境, 失败记录, 取证边界]
status: stable
generated: { by: human:wilson, at: 2026-10-07T00:00:00Z }
---

# 目的

明确记录本次知识建立过程中的**取证边界**:哪些结论有执行证据,哪些因环境限制只能推断。避免后续读者把推断误当验证事实。

# 实测失败一:ts-node 依赖损坏

命令:

```
npx ts-node src/server.ts
```

结果:

```
Error: Cannot find module './util'
Require stack:
- .../login_demo/node_modules/.bin/ts-node
code: 'MODULE_NOT_FOUND'
```

影响:`npm run dev`(`ts-node-dev` 系)在本环境**不可用**。

# 实测失败二:端口监听被拒

改用预编译产物:

```
node dist/server.js
```

结果:

```
Error: listen EPERM: operation not permitted 0.0.0.0:3001
    at Server.setupListenHandle
    at app.listen (.../express/lib/application.js:635:24)
code: 'EPERM', errno: -1, syscall: 'listen', port: 3001
```

影响:**所有需要监听 3001 端口的验证均无法进行**,包括 `GET /health`、`POST /api/login`、`GET /api/profile` 的全部 HTTP 行为。curl 一律返回连接失败(`HTTP:000`)。

# 连带失败:git 命令受限

```
git log
→ fatal: unable to access '/Users/wilson/.gitconfig': Operation not permitted
```

影响:无法通过 git 历史获取变更信息,也无法确认是否有未提交改动。本 bundle 全部结论仅基于**当前工作区文件内容**。

# 应对:改用进程内直调

为绕开上述限制,采用 require `dist/` 纯逻辑模块的方式取证。该路径不监听端口、不依赖 ts-node,已成功执行,结果见 [测试入口与验证手段](/testing/test-entry.md)。

取证前先确认 `dist/` 与 `src/` 一致:

- `dist/routes/login.js` 的 `400`/`401` 分支数与 `src/routes/login.ts` 一致。
- `dist/server.js` 仍为 `port = 3001` + `app.listen`。
- `dist/utils/token.js`、`dist/middleware/auth-middleware.js` 除 `__esModule` / `exports` 外无时间相关标识。
- `dist/db/database.js` 的 `usersTable` 两条记录与 `src/db/database.ts` 一致。

# 取证纪律声明

1. 未修改任何业务源码。
2. 未向项目写入探针文件(探针置于 `/tmp`)。
3. 未记录任何凭证或明文密码值。
4. 无执行证据的结论均已标注「待验证」,未与验证事实混写。
