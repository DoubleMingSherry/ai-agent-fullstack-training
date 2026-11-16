---
type: Fact
title: 无 token 到期规则
description: login_demo 不存在任何 token 到期(expiry/TTL)规则,签发即永久有效,且无签名校验。
tags: [login_demo, expiry, token, 负向结论]
status: stable
generated: { by: human:wilson, at: 2026-10-07T00:00:00Z }
verified: { by: human:wilson, at: 2026-10-07T00:00:00Z }
---

# 结论

**本代码库不存在 token 到期规则。** 这是一个经执行的负向验证结论,不是推断。

# 数据

对 `login_demo/` 全量检索到期相关标识:

```
grep -rniE "expir|exp\b|ttl|maxAge|iat|refresh|session|timeout|deadline|cookie" \
     src/ artifacts/ README.md package.json
```

**结果为无任何匹配(退出码 1)。**

四个层面的逐一确认:

| 层面 | 事实 |
|---|---|
| 载荷结构 | `AuthTokenPayload` 只有 `{ userId, role }`,无 `iat` / `exp` |
| 签发 `issueAccessToken` | 仅 `base64url(JSON.stringify(payload))`,不写入任何时间声明 |
| 解析 `parseAccessToken` | 仅判断前缀 + 解码 + `JSON.parse`,**无任何时间比较** |
| 中间件 `authMiddleware` | 仅判断 `payload` 是否为 null,不校验时效 |

编译产物层面同样确认:`dist/utils/token.js` 与 `dist/middleware/auth-middleware.js` 中除 `__esModule` / `exports` 外无任何时间相关标识。

# 分析

token 属于「自带身份、无签名、无时效」的透明载荷,由此可得:

1. 签发后永久有效,仅随进程/服务重启等外部因素失效。
2. 无签名校验 ⇒ 客户端可自行构造 `demo-token.<base64url({userId, role})>` 伪造任意身份(含 admin)。这是架构层的既定事实描述,而非攻击脚本。
3. 不存在刷新窗口,也不存在可调的 TTL 配置项。
4. 因此没有「到期规则」可供修改或调参;若任务要求实现到期,那是**新增能力**。

# 到期规则若要实现的挂载点

仅供后续改造参考,当前两处逻辑**均不存在**:

| 环节 | 文件 | 需新增的内容 |
|---|---|---|
| 签发时写入 | `src/utils/token.ts` | 载荷中加 `exp`(或 `iat` + TTL) |
| 校验时比较 | `src/middleware/auth-middleware.ts` | 解析后比较当前时间与 `exp`,过期返回 `401` |
| 类型约束 | `src/types/user.ts` | `AuthTokenPayload` 增加时效字段 |

# 证据强度

| 声明 | 强度 |
|---|---|
| 源码中无 expiry/TTL 检索词 | 已执行验证(全量 grep) |
| 编译产物中无时间逻辑 | 已执行验证(逐文件 grep) |
| token 永久有效、可伪造 | 从源码结构推断,【待验证】未做端到端时序测试 |
| 实现到期需改上述三处 | 设计建议,尚未落地 |

# 相关

- 会话整体定位见 [会话模块位置与调用链](/sessions/module-layout.md)。
- token 结构细节见 [Token 格式与载荷](/sessions/token-format.md)。
