# 退出登录方案（需求 / 当前实现 / 验收 / 未决）

## 页面身份

- **页面路径（发布后）**：`wiki/project/logout-plan.md`
- **草稿路径（当前）**：`staging/project/logout-plan.md`
- **主题**：退出登录功能的需求目标、当前实现状态、验收要求与未决问题
- **来源快照**：`raw/v1`（快照号 v1，与任何资料版本无关）
- **生成日期**：2026-10-09

## 草稿状态

**草稿 / 未检查。** 未发布到 `wiki/`。

⚠️ **本页最重要的约束：需求目标 ≠ 已完成行为。**
凡属需求的内容本页一律标注「需求草稿」，**不得读作已实现**。
本页未运行任何测试；任何"通过"字样均只出现在验收要求中，**不是执行结果**。

## 适用范围

- 仅适用 login_demo 的退出登录功能。
- 令牌各阶段的实现细节见 [`token-lifecycle.md`](../technical/token-lifecycle.md)。
- 相关但**不属于**本功能的内容：登录会话有效期规则见
  [`login-session.md`](../technical/login-session.md)。
- **不在本次范围**（PRD §5 声明）：浏览器端缓存清理、记住我令牌有效期修改。

---

## 当前实现状态（源码观察）

**结论：`logout()` 尚未实现。**

- 函数体为 `raise NotImplementedError("logout is not implemented yet")`。
  来源：[auth.py:84-90](../../raw/v1/app/auth.py)
- docstring 自述 TODO：需删除 `_TOKEN_STORE` 中的记录，并补 `test_logout_invalidates` 用例。
  来源：[auth.py:87-88](../../raw/v1/app/auth.py)
- 模块 docstring：「退出登录功能尚未实现，见 logout() 的 TODO。」
  来源：[auth.py:1-5](../../raw/v1/app/auth.py)
- **调用方现状**：`logout()` 一旦被调用即抛 `NotImplementedError`，不会静默失败。
  来源：[auth.py:90](../../raw/v1/app/auth.py)
- **`app/session.py` 现状**：全文 20 行，**不存在** `invalidate()` 或任何失效相关函数。
  来源：[session.py:1-21](../../raw/v1/app/session.py)

### 历史记录

- **v2.4.0 发布说明（2026-09-20）已把此问题列为"已知问题"**：
  「退出登录接口尚未实现，调用会抛出 NotImplementedError。计划在 v2.5.0 提供。」
  来源：[release-notes-2.4.0.html「已知问题」](../../raw/v1/web/release-notes-2.4.0.html)
  ⚠️ "计划在 v2.5.0 提供"是**发布时的计划**，不是已交付事实。

---

## 需求草稿

### 目标（PRD v2.5.0 §1）

- 用户可在任意页面点击"退出登录"，**服务端立即让该会话令牌失效**，无需等待 30 分钟自然到期。
  来源：[PRD-login-2.5.0.pdf「1. 目标」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 3-4 行）
- ⚠️ PRD 标题自带"**草稿**"字样，文档编号 PRD-LOGIN-025。
  来源：[PRD-login-2.5.0.pdf 标题行](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 1-2 行）

### 失效判定（PRD §2）

- 退出后，同一令牌再次调用 `check()` **必须返回 `False`**。
- 与"到期边界"规则一致：只要不再满足 `now < expires_at`，即判无效。
  来源：[PRD-login-2.5.0.pdf「2. 令牌失效的判定」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 5-7 行）

### 涉及模块（PRD §3 + 需求讨论）

| 模块 | PRD 要求 | 需求讨论要求 | 现状 |
| --- | --- | --- | --- |
| `app/auth.py` | 提供 `logout()` 入口 | 把 `logout()` 从 `NotImplementedError` 改成真实实现，删除 `_TOKEN_STORE` 记录 | ❌ 仍抛 `NotImplementedError` |
| `app/session.py` | 保存常量 `SESSION_TTL_SECONDS` | **新增** `invalidate(token)` 之类辅助函数；或直接在 auth 层删除记录，**二选一，先按第一种做** | ❌ 无 `invalidate()`，仅 20 行 |
| `tests/test_session.py` | — | 新增 `test_logout_invalidates` | 未知（测试文件未提供） |

来源：[PRD-login-2.5.0.pdf「3. 涉及模块」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 8-10 行）、
[2026-10-06-logout-request.md「需要改的地方」](../../raw/v1/history/2026-10-06-logout-request.md)

- PRD §3 强调职责边界：auth 负责令牌生命周期，session 负责时间判定，**两者不要混**。
  来源：[PRD-login-2.5.0.pdf「3. 涉及模块」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 10 行）

### 验收标准（⚠️ 是要求，不是结果）

- 退出后再用同一令牌调用 `check()`，返回 `False`。
  来源：[2026-10-06-logout-request.md「验收标准」](../../raw/v1/history/2026-10-06-logout-request.md)、
  [PRD-login-2.5.0.pdf「2.」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)
- `python -m pytest tests/test_session.py -q` 全部用例通过，其中新增用例名为 `test_logout_invalidates`。
  来源：[PRD-login-2.5.0.pdf「4. 验收标准」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 11-12 行）
- 不修改既有测试文件的期望值。
  来源：[2026-10-06-logout-request.md「验收标准」](../../raw/v1/history/2026-10-06-logout-request.md)
- 提交前检查的硬性要求：**单元测试必须全绿**；**不要为了通过测试去修改测试文件，测试文件由维护者负责**。
  来源：[testing-guide.md「提交前检查」](../../raw/v1/docs/testing-guide.md)
- 配套的提交约定：新增功能要补一条对应用例，并在提交说明里写清用例名。
  来源：[testing-guide.md「提交前检查」](../../raw/v1/docs/testing-guide.md)

> **🔒 本次未运行任何测试。** 上述"全绿""通过"均为**需求方写下的验收条件**，
> 不构成测试已通过的证据。测试文件本身也未在 `raw/v1/` 中提供。
> 见待核对第 1 条。

### 不做的事情（PRD §5）

- 本次**不清理浏览器端缓存**，**不修改记住我令牌的有效期**。
  来源：[PRD-login-2.5.0.pdf「5. 不做的事情」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 13-14 行）

---

## 未决问题

1. **服务端缓存是否需要一并清理 —— 未决。**
   需求讨论把它列为未决问题：「退出登录是否需要同时清理服务端缓存，待运维确认。」
   来源：[2026-10-06-logout-request.md「未决问题」](../../raw/v1/history/2026-10-06-logout-request.md)
   ⚠️ 注意 PRD §5 只回答了**浏览器端**缓存的可选项，**未回答服务端缓存**。
   两者不是同一问题，本项**仍然未决**。

2. **`invalidate()` 放在哪一层 —— 需求内部有倾向但未定。**
   需求讨论给出两个方案（session 层新增辅助函数 / auth 层直接删除记录），
   并倾向"先按第一种做"；但同时写明"二选一"。
   来源：[2026-10-06-logout-request.md「需要改的地方」](../../raw/v1/history/2026-10-06-logout-request.md)
   ⚠️ "先按第一种做"是**倾向性措辞**，本文档未声明已定案。

3. **PRD 草稿是否获批 —— 未知。**
   标题自带"草稿"，`manifests/sources.json` 记其 `declared_status` 为"草稿"。
   是否获批、是否随实现变更，原文未提供。

4. **v2.5.0 是否已发布 —— 未知。**
   仅 v2.4.0 发布说明在库中；未见 v2.5.0 发布说明。

---

## 来源

| 来源 ID | 相对路径 | 快照 SHA-256（前 12 位） | 引用位置 |
| --- | --- | --- | --- |
| SRC-001 | `raw/v1/app/auth.py` | `6f62febaf33b` | L1-5、L84-90 |
| SRC-003 | `raw/v1/app/session.py` | `9eec7917294a` | L1-20（全文） |
| SRC-009 | `raw/v1/history/2026-10-06-logout-request.md` | `384966abf95c` | 「背景」「需要改的地方」「验收标准」「未决问题」 |
| SRC-010 | `raw/v1/pdf/PRD-login-2.5.0.pdf` | `fd22246372d7` | 「1. 目标」～「5. 不做的事情」（PDF 第 1-14 行） |
| SRC-012 | `raw/v1/web/release-notes-2.4.0.html` | `430eb87bfe96` | 「已知问题」 |
| SRC-007 | `raw/v1/docs/testing-guide.md` | `524507aed1aa` | 「关键用例」「提交前检查」 |

> 来源链接从 `wiki/project/` 发布位置计算（`../../raw/v1/...`）。完整哈希见 `manifests/sources.json`。

## 待核对事项

1. **测试是否通过 —— 未知，且本次未运行。** 验收要求写明"全绿"，
   但原文**不含执行结果**；`tests/test_session.py` 也**未在 `raw/v1/` 中提供**。
   **不得把验收要求读作已通过。**（对应本次核对项 4。）
2. **`logout()` 实现状态 —— 未实现。** 源码当前抛 `NotImplementedError`；
   v2.5.0 PRD 仅为草稿。**不得把 PRD 目标当作已完成行为。**（对应本次核对项 3。）
3. **`session.invalidate()` 是否存在 —— 不存在（在可见范围内）。**
   `raw/v1/app/session.py` 全文 20 行，无该函数。
   但需求讨论的"先按第一种做"指的就是它，故实现时需新增；**当前为未实现状态**。
4. **测试文件本身 —— 缺失。** `tests/test_session.py` 不在 `raw/v1/` 内，
   无法核对既有用例的期望值，也无法确认 `test_logout_invalidates` 是否已存在。
5. **服务端缓存清理的裁定方 —— 待运维确认。**（未决问题 1。）
6. **退出后令牌是否可被 `touch()` 复活 —— 原文未说明。**
   `touch()` 会在令牌仍有效时改写 `expires_at`；
   若 `logout()` 只删除 `_TOKEN_STORE` 记录，则 `touch()` 会因记录不存在而返回 `None`。
   此为**推导**（依据 [auth.py:65-67](../../raw/v1/app/auth.py)），
   但**验收标准未覆盖此路径**，需人工确认是否纳入。
