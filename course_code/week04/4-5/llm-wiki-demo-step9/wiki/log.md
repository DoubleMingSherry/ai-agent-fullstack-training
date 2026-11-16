# Wiki 操作记录

按时间倒序。每条记录操作、依据与结果。

---

## 2026-10-09 — 版本更新：登录会话规则 v1 → v2（30 分钟 → 60 分钟）

**操作**：新增来源 `SRC-013`，按两阶段更新受影响的 5 个页面。

### 新来源

- 路径：`raw/v2/docs/session-rules.md`
- SHA-256：`b309f914bd46706ef3a0a80ff5247256f1f010654e84cc376c27612fa82caae1`
- 快照：`v2`（新增；**`raw/v1/` 12 份文件全部原样保留、未改动**，哈希复核一致）
- 替代：`SRC-013` **替代** `SRC-006`（v1 规则）；`SRC-006` 保留并登记 `superseded_by`
- 激活时间：**2026-10-09**
- 批准人 / 批准日期 / 签字：**均记为未知 —— 未编造任何正式审批人或签字记录**
  （原文只写「本实验已批准」；本项目无审批系统）

### 阶段一：影响评估

依据 `staging/dependencies.json` 的 `sources_used` + `page_graph` 计算依赖闭包：

- **直接受影响（3）**：`login-session.md`、`session-types.md`、`login-onboarding.md`
- **间接受影响（2）**：`token-lifecycle.md`、`logout-plan.md`
- **受影响页面全集 = 5（仓库内全部知识页）**
- 产出：`reports/update-impact.md`；`manifests/pages.json` 记录 `pending_review`

### 阶段二：页面更新（全部升至 v2）

| 页面 | v1 → v2 变更 | 新 SHA-256（前 16 位） |
| --- | --- | --- |
| `technical/login-session.md` | 规则改 60 分钟；新增「规则与源码差异」表；v1 规则移入「规则历史」 | `0cdf72f58cee524c` |
| `technical/token-lifecycle.md` | §4 续期标注规则/源码差异；§6 退出登录状态**未变** | `193bc8799563a457` |
| `comparisons/session-types.md` | 主表 login 行 + 规则更新对照；**CSV 原值 30 分钟照录并标注未同步** | `9915816fd602e03e` |
| `project/logout-plan.md` | 目标时长措辞改依规则；**退出登录状态未变** | `e02d3a8032c95877` |
| `queries/login-onboarding.md` | Q1/Q3 改 60 分钟；新增「两个有效期」对照节 | `aa19e931302bdca7` |

### 保留的实际差异（未消除）

1. **当前规则要求 60 分钟**（`SRC-013`）
2. **源码仍 `SESSION_TTL_SECONDS = 30 * 60` = 30 分钟**（`raw/v1/app/session.py:6`，**未修改**）
3. **FAQ / `session-matrix.csv` / `payment-session.md` / `testing-guide.md` 仍写 30 分钟**
   —— 标注「尚未同步」，**原文未改**
4. **退出登录实现状态未变** —— `logout()` 仍 `raise NotImplementedError`

**未做全局替换**：`30`、`1800`、`30 * 60` 等字样**未**被批量替换；
v1 规则、历史记录、发布说明中的 30 分钟均按原文保留。

### 同步

- `manifests/sources.json` — 新增 `SRC-013`；`SRC-006` 加 `superseded_by`；新增 `snapshots` 段
- `manifests/pages.json` — 5 页 `version` → 2、新哈希、`update_reason`、`current_rules_snapshot: raw/v2`
- `staging/dependencies.json` — 5 页新增 `SRC-013`、`version` → 2、新哈希；新增 4 条 v2 结论
- `wiki/index.md` — 5 页摘要、版本、来源、待核对数更新；新增「规则 vs 源码不一致」提示
- 4 个 `staging/` 草稿同步为 v2，与 `wiki/` 逐字节一致

### 未接入（必须显式记录）

- ❌ **无向量索引** —— 未对 5 页重新嵌入，不存在 embedding/向量库
- ❌ **无缓存层** —— 无页面缓存/CDN/查询缓存，**未失效任何缓存**（因不存在）
- ❌ **无搜索索引重建** —— 无倒排表，未触发 reindex
- ❌ **无审批系统** —— 「已批准」仅为原文措辞，无审批人、无签字、无留痕
- ❌ **无原子发布 / 并发控制** —— 页面为逐文件写入
- ❌ **未运行任何测试** —— 不得宣称测试通过

---

## 2026-10-09 — 新增问答页 `wiki/queries/login-onboarding.md`

**操作**：回答新人上手的 5 个登录会话问题，产出问答页并登记。

**是否更新既有页面**：先检查是否存在同类页面 —— `wiki/queries/` 不存在，
全库未发现 onboarding / 新人 / 问答 类页面。**确认为新建，未产生重复页面。**

**先读取**：`schema.md`、`manifests/sources.json`、`wiki/index.md`，
再按索引读取 4 个知识页。

**来源策略（本次重点）**：结论**直接回查 `raw/v1` 原文**（源码 / 文档 / 历史 / 需求），
**不引用其他知识页作为最终依据**；知识页仅作导航与交叉印证。已逐条回到原文核对：

| 问题 | 回查的原文 |
| --- | --- |
| Q1 有效期与边界 | `docs/session-rules.md`「有效期长度」「到期边界」+ `app/session.py` L6、L15 + `history/2026-09-12-expiry-bug.md` |
| Q2 模块分工 | `app/session.py` L3 docstring + `pdf/PRD-login-2.5.0.pdf`「3. 涉及模块」+ `app/auth.py` L14、L16-17 |
| Q3 续期触发 | `docs/session-rules.md`「续期」+ `app/auth.py` L63-72 + `app/session.py` L18-20 |
| Q4 退出登录 | `app/auth.py` L84-90、L1-5 + `web/release-notes-2.4.0.html`「已知问题」+ PRD 标题行 |
| Q5 验证入口与结果 | `docs/testing-guide.md`「测试入口」「关键用例」「提交前检查」 |

**登记与同步**：

- `manifests/pages.json` —— 新增 `PAGE-LOGIN-ONBOARDING`（版本 1、内容哈希、
  8 个来源、4 个依赖页、6 项待核对）
- `staging/dependencies.json` —— 新增该页的来源清单、引用位置、依赖页、
  16 条结论→证据映射；`page_graph` 增加 4 条 `cross_reference` 边
- `wiki/index.md` —— 新增「问答页（queries）」小节；状态说明表新增「问答页」行；
  已发布页面数更新为 5
- 本文件 —— 写入本条记录

**链接校验**：`raw/` 来源链接 26 条 + 知识页链接 5 条 = **31 条全部可解析** ✅

**本次未做**：

- **未运行任何测试，未执行任何业务修改**
- **未宣称任何测试通过** —— 问答页 Q5 明确写明“本次无任何执行结果”
- 未修改 `raw/` 任何文件（12 份来源哈希复核未变）
- 未修改 4 个既有知识页

**发布方式说明**：本页**直接在 `wiki/` 生成，无 `staging/` 草稿**；
因此未经过 `reports/prepublish.md` 那样的完整发布前检查流程，
仅做来源回查与链接校验。此为当前流程的不一致处，见下条待办。

---

## 2026-10-09 — 发布 4 个知识页（v1）

**操作**：将 4 个通过检查的草稿从 `staging/` 发布到 `wiki/`。

**依据**：`reports/prepublish.md`（结构检查 + 内容核对全部通过）。

**发布内容**：

| 页面 | 路径 | 版本 | content_sha256（前 16 位） |
| --- | --- | --- | --- |
| 登录会话 | `wiki/technical/login-session.md` | 1 | `65eff55065f0955e` |
| 令牌生命周期 | `wiki/technical/token-lifecycle.md` | 1 | `0285c5162dea6afa` |
| 会话类型对比 | `wiki/comparisons/session-types.md` | 1 | `dd571e8325d16e2d` |
| 退出登录方案 | `wiki/project/logout-plan.md` | 1 | `254d1c145c4544b2` |

**发布前基线复核**：

- `raw/` 12 份来源 SHA-256 —— 全部未变 ✅
- 发布目标 `wiki/technical|comparisons|project/` —— 均不存在，无覆盖 ✅
- 草稿基线哈希已捕获，发布后 `wiki/` 副本与 `staging/` 源逐字节一致 ✅

**同步**：

- `manifests/pages.json` —— 新建，登记 4 页的版本、状态、内容哈希、行数、来源、依赖
- `staging/dependencies.json` —— 4 页 `status` 由 `draft-unchecked` 改为 `published`，
  补充 `version` 与 `content_sha256`
- `wiki/index.md` —— 写入 4 页链接、摘要、状态、来源与待核对计数
- 本文件 —— 写入本条记录

**链接校验**（从发布后实际路径）：`raw/` 来源链接 87 条 + 跨页链接 9 条 = **96 条全部可解析** ✅

**本次未做**：

- 未运行任何业务测试
- 未修改 `raw/` 任何文件
- 未删除 `staging/` 草稿（保留为草稿区原始物）
- 未创建审批记录（本项目无审批系统）

**发布方式说明**：逐文件 `cp`，**非事务性**。未实现锁与并发控制；本次仅以
“目标文件是否存在”作为覆盖防护。详见 `manifests/pages.json` 的 `capability_limits`。

---

## 2026-10-09 — 发布前检查（pre-publish）

**操作**：对 `staging/` 4 个草稿做结构检查与内容核对，产出 `reports/prepublish.md`。

**结构检查**：来源存在性 12/12、哈希一致 12/12、结论证据覆盖 46/46、
引用位置有效（40 行号 + 33 章节，0 问题）、页面 ID 无重复、路径无越界。

**内容核对**：发现并修正 ——

- 2 处**伪引文**（把“不存在某物”的观察写进 `quote` 字段）→ 改为真引文 + `note`
- 3 处**遗漏否定词/条件**（FAQ 产品确认、升级注意、提交前检查）→ 已补齐
- 2 处**行数错误**（session.py 记 21 实为 20；config.py 记 11 实为 10）→ 已更正
- 1 处**章节引用用了省略号**（无法定位）→ 改为完整标题

**未修改**：`raw/` 任何文件；未为迎合结论而改动原文。

**结论**：4 页均可发布，无未解决冲突（schema 第 6、7 条本轮未触发 —— 无新增来源）。

---

## 2026-10-09 — 导入来源快照（v1）

**操作**：将 `corpus.zip` 内容原样导入 `raw/v1/`。

- 归档 sha256：`b63afc9409ffdb1fc8b68c4124f3078ac0bf19e0aa1db4571fabd3f414e4f5fa`
- 导入 12 个文件，保留相对目录；排除 `__MACOSX/` 元数据
- 创建 `wiki/`、`staging/`、`manifests/`、`logs/`、`reports/`
- 创建 `schema.md`（9 条规则）
- 创建 `manifests/sources.json`（12 份来源 + 5 项未解决）
- 详见 `logs/2026-10-09-import.md`

---

## 待办（未实现）

以下为**已知未实现**的能力，记录备查，不得在别处暗示其存在：

- 页面级审批流与审批留痕
- 权限模型（`owner` 字段目前仅为文本描述）
- 事务性 / 原子发布
- 并发写入锁与版本冲突检测
- 自动回归与哈希巡检（当前为一次性人工校验）
- 语义检查器（“引文是否支持结论”目前由人工/模型逐条阅读判定）
- **问答页（`wiki/queries/`）缺少对应的发布前检查流程** ——
  知识页走 `staging/` → `reports/prepublish.md` → `wiki/`；
  问答页目前直接在 `wiki/` 生成，只做来源回查与链接校验，
  未经同等的结构与内容核对。流程尚未统一。
