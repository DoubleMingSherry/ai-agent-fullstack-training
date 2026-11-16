---
okf_version: "0.2"
---

# login_demo 知识库

本 bundle 记录 `login_demo`(Express + TypeScript 登录 demo)的会话模块位置、到期规则与测试入口。内容来源为当前源码、`README.md`、`artifacts/login-flow.md` 与实际执行记录。

范围与纪律:

- 只读源码,不修改业务代码。
- 不含任何凭证、密钥或个人身份信息。
- 有执行证据的结论标注为「已执行验证」;仅从源码推断的标注为「待验证」。

## 会话与认证

* [会话模块位置与调用链](/sessions/module-layout.md) - 会话由登录签发 token 与中间件校验 token 两段组成,含全部文件定位表。
* [Token 格式与载荷](/sessions/token-format.md) - `demo-token.<base64url>` 结构与 `AuthTokenPayload` 字段。
* [无 token 到期规则](/sessions/expiry-rule-absent.md) - 经检索确认不存在 expiry/TTL,以及到期规则若需实现的挂载点。

## 验证与测试

* [测试入口与验证手段](/testing/test-entry.md) - 无测试框架,可用验证手段与已执行的无端口验证结果。
* [环境限制与失败记录](/testing/environment-limits.md) - ts-node 损坏与端口监听被拒的实测记录。

## 元信息

* [待验证事项](/log.md) - 尚未取得执行证据的结论清单。
