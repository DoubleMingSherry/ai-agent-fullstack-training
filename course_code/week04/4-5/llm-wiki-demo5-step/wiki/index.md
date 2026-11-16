# 知识页索引

login_demo 登录项目 LLM Wiki 实验。本索引列出已发布的知识页、摘要与状态。

- **快照**：`raw/v1`（快照号 v1，与资料版本无关）
- **规则**：见 [`schema.md`](../schema.md)
- **来源清单**：见 [`manifests/sources.json`](../manifests/sources.json)
- **页面注册表**：见 [`manifests/pages.json`](../manifests/pages.json)
- **最近检查**：`reports/prepublish.md`（2026-10-09）
- **操作记录**：见 [`log.md`](./log.md)

> ⚠️ **本 wiki 为本地实验。** 无审批系统、无权限模型、无原子发布、无自动回归。
> 页面状态「已发布」仅表示通过 `reports/prepublish.md` 的检查并已复制到 `wiki/`，
> **不含**任何人工审批含义。详见 `manifests/pages.json` 的 `capability_limits`。

---

## 技术页（technical）

### [登录会话（有效期、判定、续期）](./technical/login-session.md)

登录会话的 30 分钟有效期、`now == expires_at` 边界、请求触发的续期方式，以及测试入口。
含 v2.4.0 的到期判定变更与 2026-09-12 故障记录。

- 状态：**已发布** · 版本 1 · 2026-10-09
- 来源：SRC-001、SRC-003、SRC-004、SRC-006、SRC-007、SRC-008、SRC-012
- 待核对 5 项（含“测试是否实际通过 —— 未知”）

### [令牌生命周期（签发、校验、检查、续期、清理、退出）](./technical/token-lifecycle.md)

令牌的六个阶段实现：`issue_token` / `verify_token` / `check` / `touch` / `purge_expired`，
以及尚未实现的 `logout`。含 auth 与 session 的职责划分、`SESSION_TTL_MINUTES` 配置接线核查。

- 状态：**已发布** · 版本 1 · 2026-10-09
- 来源：SRC-001、SRC-002、SRC-003、SRC-010
- 待核对 6 项（含“配置是否接入有效期计算”）

## 对比页（comparisons）

### [会话类型对比（登录 / 支付 / 匿名浏览 / 记住我）](./comparisons/session-types.md)

四种会话在服务归属、有效期、可续期性与到期边界处理上的区别。明确不混用边界。

- 状态：**已发布** · 版本 1 · 2026-10-09
- 来源：SRC-003、SRC-005、SRC-006、SRC-010、SRC-011
- 待核对 5 项（含匿名浏览 / 记住我仅有 CSV 单行来源）

## 项目页（project）

### [退出登录方案（需求 / 当前实现 / 验收 / 未决）](./project/logout-plan.md)

退出登录的需求目标、当前 `logout()` 未实现的状态、验收要求（非结果）与未决问题。

- 状态：**已发布** · 版本 1 · 2026-10-09
- 来源：SRC-001、SRC-003、SRC-007、SRC-009、SRC-010、SRC-012
- 待核对 6 项（含服务端缓存是否清理 —— 未决）

---

## 页面状态说明

| 状态 | 含义 |
| --- | --- |
| 已发布 | 通过 `reports/prepublish.md` 检查，已复制到 `wiki/` |
| 草稿 | 位于 `staging/`，尚未通过检查或尚未发布 |
| 待核对 | 页面内记录的未解决缺口，随页面一同发布，不作为确定事实 |

当前无 `staging/` 草稿待发布 —— 4 个草稿均已发布，内容哈希与 `wiki/` 副本一致
（见 `manifests/pages.json`）。

## 未解决事项汇总

以下缺口随页面发布保留，**不得**升格为确定事实：

1. 测试是否实际通过 —— 未知（原文无执行结果，本次未运行）
2. `config.SESSION_TTL_MINUTES` 是否接入有效期计算 —— 待核对
3. `X-Session-Expires-At` 响应头产出位置 —— 待核对
4. 匿名浏览会话 / 记住我令牌的实现 —— 未知（仅 CSV 单行）
5. PRD 草稿是否获批、v2.5.0 是否已发布 —— 未知
6. 服务端缓存是否需清理 —— 未决
7. `invalidate()` 归属层 —— 未定案
8. 签名密钥硬编码、`_TOKEN_STORE` 进程内存储 —— 未裁定
9. `purge_expired()` 的调用者 —— 未知
10. CSV 自身的日期/版本/状态 —— 未知
11. “管理后台”与“登录服务”的关系 —— 未知
