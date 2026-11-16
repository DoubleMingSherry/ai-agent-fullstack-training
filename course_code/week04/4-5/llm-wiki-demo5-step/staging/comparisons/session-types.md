# 会话类型对比（登录 / 支付 / 匿名浏览 / 记住我）

## 页面身份

- **页面路径（发布后）**：`wiki/comparisons/session-types.md`
- **草稿路径（当前）**：`staging/comparisons/session-types.md`
- **主题**：四种会话类型在服务归属、有效期、可续期性、到期边界处理上的区别
- **来源快照**：`raw/v1`（快照号 v1，与任何资料版本无关）
- **生成日期**：2026-10-09

## 草稿状态

**草稿 / 未检查。** 未发布到 `wiki/`。
本页以 `session-matrix.csv` 为主表，配合登录、支付两份文档交叉核对。
**未对匿名浏览会话与记住我令牌做任何补充推断。**

## 适用范围

- 覆盖四种会话：登录会话、支付会话、匿名浏览会话、记住我令牌。
- **本页只做对比，不合并规则。** 按 schema 第 7 条，四种类型的有效期与续期规则
  **互不通用**，不得互相套用。
- 登录会话细节见 [`login-session.md`](../technical/login-session.md)；
  支付会话细节见下文；令牌实现见 [`token-lifecycle.md`](../technical/token-lifecycle.md)。

---

## 主表（列含义保留原文）

来源：[session-matrix.csv](../../raw/v1/tables/session-matrix.csv)（6 列，4 行数据）

列含义（照原文表头，不重新解释）：
`服务` = 归属服务；`会话类型` = 会话种类；`有效期` = 时长；
`可续期` = 是否能续期（是/否）；`到期边界处理` = 恰好到期时的处理方式；`负责人` = 负责团队。

| 服务 | 会话类型 | 有效期 | 可续期 | 到期边界处理 | 负责人 |
| --- | --- | --- | --- | --- | --- |
| login_demo | 登录会话 | 30 分钟 | 是 | 恰好到期视为无效 | 认证组 |
| pay_gateway | 支付会话 | 5 分钟 | 否 | 到期即置为超时 | 支付组 |
| login_demo | 匿名浏览会话 | 120 分钟 | 是 | 到期后重新签发 | 认证组 |
| login_demo | 记住我令牌 | 30 天 | 是 | 到期需重新登录 | 认证组 |

> ⚠️ **同一张表、不同服务。** 第 2 行的服务是 `pay_gateway`，**不属于登录服务**。
> 按 schema 第 7 条，跨服务的"看起来相似"不算重复，不得合并其规则。

### 交叉核对（原文事实）

| 核对点 | CSV 说法 | 文档说法 | 结论 |
| --- | --- | --- | --- |
| 登录会话有效期 | 30 分钟 | 「有效期为 30 分钟」[session-rules.md](../../raw/v1/docs/session-rules.md) | ✅ 一致 |
| 登录会话可续期 | 是 | 「空闲不自动续期，必须由用户请求触发」[session-rules.md](../../raw/v1/docs/session-rules.md) | ✅ 一致（续期需请求触发） |
| 登录会话到期边界 | 恰好到期视为无效 | 「`now == expires_at` 属于已过期」[session-rules.md](../../raw/v1/docs/session-rules.md) | ✅ 一致 |
| 支付会话有效期 | 5 分钟 | 「支付会话有效期为 5 分钟」[payment-session.md](../../raw/v1/docs/payment-session.md) | ✅ 一致 |
| 支付会话可续期 | 否 | 「到期后不刷新，只能重建」[payment-session.md](../../raw/v1/docs/payment-session.md) | ✅ 一致 |

---

## 分类型说明

### 登录会话（login_demo）

- 有效期 30 分钟，从签发时刻起算；可续期，但须由用户请求触发。
  来源：[session-rules.md「有效期长度」](../../raw/v1/docs/session-rules.md)
- 到期边界 `now == expires_at` 为无效；实现为 `now < expires_at`。
  来源：[session-rules.md「到期边界」](../../raw/v1/docs/session-rules.md)、
  [session.py:15](../../raw/v1/app/session.py)
- 续期后新到期时间 = 请求到达时刻 + 30 分钟。
  来源：[session-rules.md「续期」](../../raw/v1/docs/session-rules.md)

### 支付会话（pay_gateway）

- 有效期 5 分钟；超时后订单进入 `PAY_TIMEOUT` 状态，需用户重新下单。
  来源：[payment-session.md「有效期长度」](../../raw/v1/docs/payment-session.md)
- **不可续期**：到期后不刷新，只能重建。
  来源：[payment-session.md「到期边界」](../../raw/v1/docs/payment-session.md)
- 到期边界为 `now >= expires_at` 时置为超时。
  来源：[payment-session.md「到期边界」](../../raw/v1/docs/payment-session.md)
- **与登录服务无关**，原文明确声明。两者共用 `expires_at` 字段名，
  排查时须先确认是哪个服务的会话。
  来源：[payment-session.md](../../raw/v1/docs/payment-session.md)（正文首段、「与登录会话的区别」）

### 匿名浏览会话（login_demo）

- 有效期 120 分钟；可续期；到期后重新签发。
  来源：[session-matrix.csv](../../raw/v1/tables/session-matrix.csv) 第 4 行
- **唯一来源是 CSV 一行。** 其余 11 份来源均未展开描述该类型。
  它在 `session-rules.md` 中只被**排除**：「本节规则只约束登录会话，
  不适用于支付会话、匿名浏览会话。」
  来源：[session-rules.md「不适用范围」](../../raw/v1/docs/session-rules.md)
- **因此：登录会话的续期与边界规则不可套用到本类型。** 本类型的
  "重新签发" 与登录会话的 "刷新 `expires_at`" 不是同一机制，原文未说明其细节。

### 记住我令牌（login_demo）

- 有效期 30 天；可续期；到期需重新登录。
  来源：[session-matrix.csv](../../raw/v1/tables/session-matrix.csv) 第 5 行
- **唯一来源同样是 CSV 一行。** 源码与文档中均未见其实现或详述。
- v2.5.0 PRD 明确将本类型列为**本次不动**的对象：「不修改记住我令牌的有效期」。
  来源：[PRD-login-2.5.0.pdf「5. 不做的事情」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 14 行）
- **同样不得套用登录会话的 30 分钟与边界规则。**

---

## 明确的不混用边界（本次特别核对项 7）

按 schema 第 7 条与来源自身声明，以下**均不得混用**：

1. **有效期时长**：30 分钟 ≠ 5 分钟 ≠ 120 分钟 ≠ 30 天。四者各自独立。
2. **可续期性**：登录/匿名/记住我 可续期，支付**不可**续期。
3. **到期边界动作**：登录=判无效；支付=置超时；匿名=重新签发；记住我=需重新登录。
   四种"到期后做什么"各不相同，`expires_at` 字段名相同**不代表**语义相同。
4. **服务归属**：支付会话属 `pay_gateway`，**不是**登录服务的一部分。
5. **匿名浏览与记住我**：仅有 CSV 单行描述，**不足以**推出实现细节。

---

## 来源

| 来源 ID | 相对路径 | 快照 SHA-256（前 12 位） | 引用位置 |
| --- | --- | --- | --- |
| SRC-011 | `raw/v1/tables/session-matrix.csv` | `e15dbc93c9dd` | 表头 + 第 2-5 行（全部数据） |
| SRC-006 | `raw/v1/docs/session-rules.md` | `bfa3dbaa5c7b` | 「有效期长度」「到期边界」「续期」「不适用范围」 |
| SRC-005 | `raw/v1/docs/payment-session.md` | `9354416930cf` | 「有效期长度」「到期边界」「与登录会话的区别」 |
| SRC-003 | `raw/v1/app/session.py` | `9eec7917294a` | L15（登录会话边界实现） |
| SRC-010 | `raw/v1/pdf/PRD-login-2.5.0.pdf` | `fd22246372d7` | 「5. 不做的事情」（PDF 第 13-14 行） |

> 来源链接从 `wiki/comparisons/` 发布位置计算（`../../raw/v1/...`）。完整哈希见 `manifests/sources.json`。

## 待核对事项

1. **匿名浏览会话的实现 —— 未知。** 仅 CSV 一行；有效期 120 分钟、可续期、
   重新签发的**具体机制**原文未说明。不得从登录会话规则外推。
2. **记住我令牌的实现 —— 未知。** 仅 CSV 一行；30 天如何计算、
   "到期需重新登录"与"可续期"如何并存，原文未说明。
3. **CSV 本身的来源与时间 —— 未知。** `session-matrix.csv` 无 frontmatter，
   文件级未声明日期、版本、状态（见 `manifests/sources.json` SRC-011）。
   表中数据与哪份文档同期，**待核对**。
4. **支付会话 `now >= expires_at` 与登录会话 `now == expires_at` 的关系 —— 待核对。**
   两者在"恰好到期"这一点上都判为失效/超时，但支付文档只写了 `>=`，
   `>` 与 `==` 的区分是否被实现覆盖，原文未说明。
5. **CSV 与文档若冲突如何取舍 —— 未发生，但规则待明确。**
   本次核对（见「交叉核对」表）**未发现冲突**；若将来出现，按 schema 第 7 条
   显式记录，不静默取一方。
