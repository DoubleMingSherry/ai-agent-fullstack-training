# 更新日志

## 2026-10-07

* **Creation**: 建立 [会话模块位置与调用链](/sessions/module-layout.md),定位会话相关全部文件与登录/鉴权两条链路。
* **Creation**: 建立 [无 token 到期规则](/sessions/expiry-rule-absent.md),记录经全量检索确认的负向事实。
* **Creation**: 建立 [Token 格式与载荷](/sessions/token-format.md),记录 `demo-token.<base64url>` 结构与实测往返结果。
* **Creation**: 建立 [测试入口与验证手段](/testing/test-entry.md),确认本项目无任何测试框架。
* **Creation**: 建立 [环境限制与失败记录](/testing/environment-limits.md),记录取证边界与失败命令原文。
* **Decision**: 因环境禁止监听端口,端到端 HTTP 行为一律标为「待验证」,不以推断充作验证事实。
* **Decision**: 未修改业务源码;探针脚本置于 `/tmp`,不写入项目。
* **Decision**: 不记录 `usersTable` 中的明文密码值,只记录其存放形式。

## 待验证事项(Questions for maintainers)

以下结论尚无执行证据,后续具备条件时应补验。

1. **HTTP 层全部行为**:`GET /health`、`POST /api/login` 各分支、`GET /api/profile` 各分支的实际状态码与响应体。当前状态码均为源码推断。
2. **token 永久有效**:仅由「源码无时间比较」推断,未做跨时间端到端实测。
3. **伪造 token 可获 admin**:由「解析不做结构/签名校验」推断,未实测。
4. **`parseAccessToken` 对结构合法但字段缺失的 JSON 的处理**:仅知会返回该对象,未验证中间件后续行为。
5. **`dist/` 与 `src/` 的完整一致性**:只对关键文件做了抽样比对,未做逐字节 diff。
6. **项目 git 历史与未提交改动**:git 命令受环境限制未能执行。
7. **`usersTable` 明文密码写法的教学意图**:从注释与结构推断,未取得作者确认。
