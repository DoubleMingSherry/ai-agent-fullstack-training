# 新人上手问答（登录会话）

## 页面身份

- **页面路径**：`wiki/queries/login-onboarding.md`
- **类型**：问答页（queries）
- **主题**：新人入职 login_demo 时最常问的 5 个登录会话问题
- **来源快照**：`raw/v1` + `raw/v2`（快照号与任何资料版本无关）
- **生成日期**：2026-10-09
- **页面版本**：**2**（v1 → v2，Q1 / Q3 随 `SRC-013` 规则更新改为 60 分钟；**Q4 退出登录状态未变**）

### 版本历史

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 1 | 2026-10-09 | 首次发布，依据 v1 规则（30 分钟） |
| 2 | 2026-10-09 | Q1 有效期与 Q3 续期时长改为 **60 分钟**（`SRC-013`）；Q2 / Q4 / Q5 结论未变；新增「规则 vs 源码」提示 |

## 页面状态

**已发布** · 版本 2 · 2026-10-09

本页为**问答转述页**。按本次要求，每条关键结论**不引用其他知识页作为最终依据**，
而是直接回查 `raw/v1` 原文（源码 / 文档 / 历史 / 需求），并在文末给出
「问答结论 → 知识页 → 原文位置」三层溯源。

## 适用范围

- 仅覆盖 **login_demo 登录会话**及其令牌。
- **不适用**：支付会话（pay_gateway）、匿名浏览会话、记住我令牌。四者规则互不通用，
  见 [`session-types.md`](../comparisons/session-types.md)。
- 本页只回答“现状是什么”，**不提供改造方案**，也不判定任何设计好坏。

---

## 先看这里：有两个“有效期”，不是一个

登录会话存在**两个不同的数**，回答问题时必须先区分问的是哪一个：

| | 时长 | 依据 | 状态 |
| --- | --- | --- | --- |
| **当前批准的有效期（规则）** | **60 分钟** | `SRC-013` `raw/v2/docs/session-rules.md` | 已批准（原文语），2026-10-09 激活，替代 v1 |
| **源码实际实现的有效期** | **30 分钟** | `SRC-003` `raw/v1/app/session.py:6` = `30 * 60` | 源码**未修改** |

**为什么会有两个数**：v2 原文自述「本资料只改变规则，不证明源码已经修改或测试已经通过。」
即 —— **规则先改，代码未跟进**。这不是记录错误，是真实差异，本页与相关知识页**并列保留**，
不做全局替换、不改写源码、也不把规则降级。

> ⚠️ 除源码外，原文中还有 **FAQ、`session-matrix.csv`、`payment-session.md`、`testing-guide.md`**
> 仍写 30 分钟，**均尚未同步**。按 schema 第 1 条，`raw/` 原文照录、不修改。
> 完整清单见 [`reports/update-impact.md`](../../reports/update-impact.md)。

---

## 问题 1：登录会话有效期多久？恰好到期是否有效？

**答：当前有效规则是 60 分钟。恰好到期（`now == expires_at`）判定为无效。
注意：代码里实际仍是 30 分钟 —— 规则与实现不一致。**

### 规则部分（当前有效）

- 原文事实（**当前有效规则**）：「登录会话有效期为60分钟。」
  来源：[session-rules.md **v2**](../../raw/v2/docs/session-rules.md)
  该文件原文状态「本实验已批准」，自 **2026-10-09** 激活，**替代 v1 登录规则**。
- 原文事实（边界，**未变**）：「now == expires_at时仍判定为无效。」
  来源：[session-rules.md v2](../../raw/v2/docs/session-rules.md)
- 原文事实（v2 自述）：「本资料只改变规则，不证明源码已经修改或测试已经通过。」
  来源：[session-rules.md v2 末条](../../raw/v2/docs/session-rules.md)

### 实现部分（源码实况，与规则不一致）

- 源码观察：常量 `SESSION_TTL_SECONDS = 30 * 60` —— **仍是 30 分钟**。
  来源：[session.py:6](../../raw/v1/app/session.py)
  🔺 规则要求 60 分钟，源码未改。这是真实差异，**并列保留**。
- 源码观察：有效条件实现为 `return now < expires_at`，与 v2 的边界规则**一致**。
  来源：[session.py:15](../../raw/v1/app/session.py)（函数 `is_session_valid`）

### 旧规则（已被替代，保留对照）

- v1 规则为「管理后台的登录会话有效期为 30 分钟」；「从签发时刻开始计算，30 分钟后到期」。
  来源：[session-rules.md **v1**](../../raw/v1/docs/session-rules.md)
  ⚠️ 该文件**已被 `SRC-013` 替代**，但**保留在 `raw/v1/` 未修改**。
- 历史记录：2026-09-12 曾因判断写成 `now <= expires_at` 导致“剩 0 秒仍可访问”，
  已把 `<=` 改为 `<`；该记录结论为“到期时刻必须判定为无效，这条结论对登录会话长期有效”。
  来源：[2026-09-12-expiry-bug.md「排查 / 修复 / 结论」](../../raw/v1/history/2026-09-12-expiry-bug.md)
  ⚠️ 该记录描述**当时**的修复过程；**当前实现以上面的源码观察为准**。

### 三点须注意（不要误解）

- **“当前批准的有效期”与“源码实际实现的有效期”不是同一个数** —— 规则 60 分钟，源码 30 分钟。
  问“有效多久”时要先说明问的是哪一个。
- v2 只说「适用服务：login_demo」，**未再提**“管理后台”，也未复述“从签发时刻起算”。
  适用范围是否仍限管理后台，原文未说明 —— **待核对**。
- 原文尚有**多处仍写 30 分钟且尚未同步**（FAQ、CSV、payment-session、testing-guide）。
  本次**不修改原文**，仅记录 —— **待核对**，详见 `reports/update-impact.md`。

---

## 问题 2：哪个模块判断时间？哪个模块管理令牌？

**答：`app/session.py` 负责**时间判断**（有效期是否仍满足）；`app/auth.py` 负责**令牌生命周期**（签发、校验、续期、失效）。两者职责不要混。**

- 原文事实（源码自述）：「本模块只负责“令牌是否仍然有效”的判断，不负责签发和存储。」
  来源：[session.py:3](../../raw/v1/app/session.py)（模块 docstring）
- 原文事实（需求资料）：「两个文件的职责不要混：auth 负责令牌生命周期，session 负责时间判定。」
  来源：[PRD-login-2.5.0.pdf「3. 涉及模块」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 9-10 行）
  ⚠️ 该 PRD 标题自带“**草稿**”字样，是需求草稿；此处引用它是因为它**明确写出了职责划分**，
  而该划分同时被**源码自身**印证（见下条）。
- 源码观察：`app/session.py` 全文只有 1 个常量 + 2 个纯函数
  （`SESSION_TTL_SECONDS`、`is_session_valid`、`refresh_expiry`），确实不含签发与存储。
  来源：[session.py:1-20](../../raw/v1/app/session.py)（全文 20 行）
- 源码观察：`app/auth.py` 持有令牌存储 `_TOKEN_STORE` 与密钥 `_SECRET`，
  签发 `issue_token()`、校验 `verify_token()`、续期 `touch()`、清理 `purge_expired()`
  均在本模块内。
  来源：[auth.py:16-17, 25-35, 38-43, 63-72, 75-81](../../raw/v1/app/auth.py)
- 源码观察：`auth.py` 通过 `from .session import SESSION_TTL_SECONDS, is_session_valid, refresh_expiry`
  使用 session 层，方向是 auth → session。
  来源：[auth.py:14](../../raw/v1/app/auth.py)

**结论**：职责划分**同时**有需求资料声明与源码印证，两者一致。

---

## 问题 3：用户请求怎样触发续期？

**答：由用户请求触发 `app/auth.py` 的 `touch()`；`touch()` 先确认会话仍有效，再把到期时间改写为“请求时刻 + 规则时长”。空闲会话不会自动续期。**

> ⚠️ **规则时长为 60 分钟，但源码常量仍是 30 分钟**，所以续期**实际**仍加 30 分钟。
> 两者不一致，原因见问题 1。

- 原文事实（规则，**未变部分**）：「空闲会话不自动续期，由用户请求触发刷新。」
  来源：[session-rules.md **v2**](../../raw/v2/docs/session-rules.md)
- 原文事实（规则，**已变部分**）：「续期后的到期时间为请求到达时刻加60分钟。」
  来源：[session-rules.md **v2**](../../raw/v2/docs/session-rules.md)
  （v1 为「新的到期时间 = 请求到达时刻 + 30 分钟」——
  [session-rules.md v1](../../raw/v1/docs/session-rules.md)，**已被替代，保留对照**。）
- 源码观察（实现）：`touch(token, now)` 的流程为 ——
  1. 令牌不存在 → 返回 `None`（不续期）；
  2. **已过期则返回 `None`**（`if not is_session_valid(...): return None`）—— 即过期令牌**不能**靠续期复活；
  3. 仍有效 → `record["expires_at"] = refresh_expiry(now)`，返回新到期时间。
  来源：[auth.py:63-72](../../raw/v1/app/auth.py)
- 源码观察：续期计算本身在 session 层 —— `refresh_expiry(now)` 返回 `now + SESSION_TTL_SECONDS`。
  来源：[session.py:18-20](../../raw/v1/app/session.py)
  🔺 因 `SESSION_TTL_SECONDS` 仍为 `30 * 60`（[session.py:6](../../raw/v1/app/session.py)），
  **实现实际加 30 分钟，而非规则的 60 分钟**。
  注意源码写的是 `now + SESSION_TTL_SECONDS`（引用常量），**不是**硬编码 1800 秒；
  常量若改为 `60 * 60` 语义即与规则一致 —— 但**本次未修改源码**。
- 推导：v2 规则里的“请求到达时刻 + 60 分钟”与实现的 `refresh_expiry(now)` **语义形式一致**
  （`now` 由调用方传入，缺省取 `time.time()`），但**取值不一致**，因常量未跟进。
  依据：[session-rules.md v2](../../raw/v2/docs/session-rules.md) + [auth.py:71](../../raw/v1/app/auth.py) + [session.py:6](../../raw/v1/app/session.py) + [session.py:20](../../raw/v1/app/session.py)
  ⚠️ **“响应返回前”这一时序**在本页可见代码中**无法核对** ——
  `touch()` 只负责改写 `expires_at`，它由谁在何处调用、是否真的在响应返回前调用，
  原文未提供。**待核对**，不得断言。

---

## 问题 4：退出登录是否已经实现？

**答：尚未实现。`logout()` 当前直接抛出 `NotImplementedError`。**

- 源码观察（当前实现）：
  ```python
  def logout(token: str) -> None:
      raise NotImplementedError("logout is not implemented yet")
  ```
  来源：[auth.py:84-90](../../raw/v1/app/auth.py)
- 源码观察：模块 docstring 自述「退出登录功能尚未实现，见 logout() 的 TODO。」
  来源：[auth.py:1-5](../../raw/v1/app/auth.py)
- 源码观察：函数 docstring 的 TODO 写明做法 ——「需要删除 `_TOKEN_STORE` 中的记录，
  并按 docs/testing-guide.md 补一条 test_logout_invalidates 用例。」
  来源：[auth.py:87-88](../../raw/v1/app/auth.py)
- 源码观察：`app/session.py` 内**不存在** `invalidate()` 之类的失效辅助函数（全文 20 行）。
  来源：[session.py:1-20](../../raw/v1/app/session.py)
- 历史记录：v2.4.0 发布说明（2026-09-20）已把它列为**已知问题** ——
  「退出登录接口尚未实现，调用会抛出 NotImplementedError。计划在 v2.5.0 提供。」
  来源：[release-notes-2.4.0.html「已知问题」](../../raw/v1/web/release-notes-2.4.0.html)
  ⚠️ “计划在 v2.5.0 提供”是**发布当时的计划**，**不是**已交付事实。

**需求状态（须与实现状态分开看）：**

- 退出登录的需求写在 v2.5.0 PRD 中，但该 PRD 标题自带“**草稿**”字样，文档编号 PRD-LOGIN-025。
  来源：[PRD-login-2.5.0.pdf 标题行](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 1-2 行）
- 还有一份 2026-10-06 的需求讨论记录，属需求草稿。
  来源：[2026-10-06-logout-request.md](../../raw/v1/history/2026-10-06-logout-request.md)
- **因此：需求存在，但均为草稿；实现不存在。** 不得把 PRD 目标读作已完成行为。

---

## 问题 5：修改后应该用什么入口验证？目前有没有本次执行结果？

**答：统一入口是 `python -m pytest tests/test_session.py -q`，运行前需先激活虚拟环境。
但 —— 目前**没有任何本次执行结果**；库中不含测试文件，也不含任何运行记录。**

- 原文事实（入口）：「统一入口：`python -m pytest tests/test_session.py -q`」；
  「不要直接敲 `pytest tests/test_session.py`，项目的 CI 只认 `python -m pytest` 形式。」；
  「运行前先激活虚拟环境：`source .venv/bin/activate`。」
  来源：[testing-guide.md「测试入口」](../../raw/v1/docs/testing-guide.md)
- 原文事实（相关用例名）：
  - `test_expire_boundary`：覆盖 `now == expires_at`，期望返回 `False`；
  - `test_refresh_extends_ttl`：覆盖续期后到期时间被推后 30 分钟；
    ⚠️ **原文仍写 30 分钟，尚未同步到 v2 的 60 分钟**。原文照录，不修改。
  - `test_logout_invalidates`：退出登录后，同一个会话令牌应判定为无效。
  来源：[testing-guide.md「关键用例」](../../raw/v1/docs/testing-guide.md)
- 原文事实（提交要求）：「单元测试必须全绿。」；
  「不要为了通过测试去修改测试文件，测试文件由维护者负责。」
  来源：[testing-guide.md「提交前检查」](../../raw/v1/docs/testing-guide.md)

### ⚠️ 关于“本次执行结果” —— 没有，请勿误读

- **本次未运行任何测试。** 本页所有内容均为静态核对（读源码、读文档），无任何执行动作。
- **库中没有测试文件本身**：`tests/test_session.py` **不在** `raw/v1/` 内，
  因此既无法核对既有用例的期望值，也无法确认 `test_logout_invalidates` 是否已存在。
- **历史记录里的“通过”不等于本次结果**：`2026-09-12-expiry-bug.md`「修复」自述
  曾运行 `python -m pytest tests/test_session.py -q` 且用例通过 —— 那是**历史自述**，
  本次**未复跑**，**不得**当作当前状态。
- `testing-guide.md` 与 PRD 里的“全绿 / 全部用例通过”是**验收要求**，
  是“应该达到什么”，**不是**“已经达到”。两处均无执行结果。
- 另有两项相关缺口：**`config.SESSION_TTL_MINUTES` 是否接入有效期计算 —— 待核对**
  （可见模块中无调用关系，但读取点可能在未提供的文件里）；
  **`X-Session-Expires-At` 响应头的产出位置 —— 待核对**（v2.4.0 发布说明称已新增，
  可见模块中没有产生该头的代码）。二者均**不推断为“不存在”**。

---

## 来源

| 来源 ID | 相对路径 | 快照 SHA-256（前 12 位） | 本页引用位置 |
| --- | --- | --- | --- |
| **SRC-013** | `raw/v2/docs/session-rules.md` | `b309f914bd46` | 全文（**当前有效规则**：60 分钟、边界仍无效、+60 分钟；替代 SRC-006） |
| SRC-001 | `raw/v1/app/auth.py` | `6f62febaf33b` | L1-5、L14、L16-17、L25-35、L38-43、L63-72、L75-81、L84-90 |
| SRC-003 | `raw/v1/app/session.py` | `9eec7917294a` | L1-4、L6（**源码仍 30 分钟**）、L9-15、L18-20（全文 20 行） |
| SRC-006 | `raw/v1/docs/session-rules.md` | `bfa3dbaa5c7b` | 「有效期长度」「到期边界」「续期」「不适用范围」（**已被 SRC-013 替代**，保留为旧值对照） |
| SRC-007 | `raw/v1/docs/testing-guide.md` | `524507aed1aa` | 「测试入口」「关键用例」「提交前检查」 |
| SRC-008 | `raw/v1/history/2026-09-12-expiry-bug.md` | `776a7b569663` | 「现象」「排查」「修复」「结论」 |
| SRC-009 | `raw/v1/history/2026-10-06-logout-request.md` | `384966abf95c` | 「背景」「需要改的地方」「验收标准」「未决问题」 |
| SRC-010 | `raw/v1/pdf/PRD-login-2.5.0.pdf` | `fd22246372d7` | 标题行、「3. 涉及模块」（PDF 第 1-2、9-10 行） |
| SRC-012 | `raw/v1/web/release-notes-2.4.0.html` | `430eb87bfe96` | 「变更内容」「已知问题」 |

> 来源链接从 `wiki/queries/` 发布位置计算（`../../raw/v1/...`、`../../raw/v2/...`）。完整哈希见 `manifests/sources.json`。

### 依赖的知识页

本页结论**直接来自 `raw/v1` 与 `raw/v2` 原文**，知识页仅作为导航与交叉印证，**不作最终依据**。

| 知识页 | 关系 |
| --- | --- |
| [`technical/login-session.md`](../technical/login-session.md) | 问题 1、3、5 的展开；其「规则与源码差异」一节与本页 Q1/Q3 一致 |
| [`technical/token-lifecycle.md`](../technical/token-lifecycle.md) | 问题 2、3、4 的展开 |
| [`project/logout-plan.md`](../project/logout-plan.md) | 问题 4 的需求侧展开 |
| [`comparisons/session-types.md`](../comparisons/session-types.md) | 问题 1 的适用范围边界 |

---

## 待核对事项（本页保留的缺口）

1. **测试是否实际通过 —— 未知。** 原文无执行结果；测试文件未提供；本次未运行。
2. **源码是否会跟进到 60 分钟 —— 未知（v2 更新引入）。** v2 自述「不证明源码已经修改」；源码仍 `30 * 60`。
3. **原文中仍写 30 分钟的其它位置何时同步 —— 待核对。** FAQ、CSV、payment-session、testing-guide 均未同步。见 `reports/update-impact.md`。
4. **“响应返回前刷新”的时序 —— 待核对。** `touch()` 的实现可见，但调用时机与调用方未提供。
5. **`config.SESSION_TTL_MINUTES` 是否接入有效期计算 —— 待核对。**
6. **`X-Session-Expires-At` 响应头产出位置 —— 待核对。**
7. **v2 的适用范围 —— 待核对。** v2 只说「适用服务：login_demo」，未再提“管理后台”，也未复述“从签发时刻起算”。
8. **PRD 草稿是否获批、v2.5.0 是否已发布 —— 未知。**
