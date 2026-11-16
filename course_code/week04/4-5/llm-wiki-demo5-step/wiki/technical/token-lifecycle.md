# 令牌生命周期（签发、校验、检查、续期、清理、退出）

## 页面身份

- **页面路径（发布后）**：`wiki/technical/token-lifecycle.md`
- **草稿路径（当前）**：`staging/technical/token-lifecycle.md`
- **主题**：login_demo 令牌的六个阶段 —— 签发、签名校验、有效性检查、续期、过期清理、退出登录
- **来源快照**：`raw/v1`（快照号 v1，与任何资料版本无关）
- **生成日期**：2026-10-09

## 草稿状态

**草稿 / 未检查。** 未发布到 `wiki/`。本页 §职责划分 与 §配置接线 含**推导**，已逐条标注。

## 适用范围

- 仅适用 **login_demo** 的令牌生命周期。
- 支付会话不适用（`pay_gateway`，见 `session-types.md`）。
- 退出登录的完整需求与未决问题另见 [`logout-plan.md`](../project/logout-plan.md)。
- 有效期与到期边界的规则细节另见 [`login-session.md`](./login-session.md)。

---

## 关键结论 / 源码观察

### 1. 签发 —— `issue_token()` ✅ 已实现

- 令牌格式为 `user_id.issued_at.随机hex.签名` 四段，以 `.` 分隔。
  来源：[auth.py:28-29](../../raw/v1/app/auth.py)
- 随机部分来自 `secrets.token_hex(8)`。
  来源：[auth.py:28](../../raw/v1/app/auth.py)
- 签发时把 `user_id`、`issued_at`、`expires_at` 写入 `_TOKEN_STORE`，
  其中 `expires_at = refresh_expiry(now)`。
  来源：[auth.py:30-34](../../raw/v1/app/auth.py)
- 令牌存储是**进程内字典** `_TOKEN_STORE: dict[str, dict]`，签发与存储均在本模块内。
  来源：[auth.py:16](../../raw/v1/app/auth.py)

### 2. 签名校验 —— `verify_token()` ✅ 已实现

- 先用段数校验：`token.count(".") != 3` 直接判为无效。
  来源：[auth.py:40-41](../../raw/v1/app/auth.py)
- 再用 `hmac.compare_digest` 做**常数时间**比较，防时序侧信道。
  来源：[auth.py:43](../../raw/v1/app/auth.py)
- 签名算法 `_sign()` 为 HMAC-SHA256，取十六进制前 16 位。
  来源：[auth.py:20-22](../../raw/v1/app/auth.py)
- 签名密钥 `_SECRET = b"login-demo-secret"` **硬编码在源码中**。
  来源：[auth.py:17](../../raw/v1/app/auth.py)
  ⚠️ 这是源码观察到的现状，不代表这是可接受的做法；见待核对第 3 条。

### 3. 有效性检查 —— `check()` ✅ 已实现

- 流程为：先 `verify_token` → 再查 `_TOKEN_STORE`（不存在则 False）→ 再 `is_session_valid`。
  来源：[auth.py:46-54](../../raw/v1/app/auth.py)
- 时间判断交由 `app/session.py` 的 `is_session_valid`，本层不重复实现
  `now < expires_at`。
  来源：[auth.py:54](../../raw/v1/app/auth.py)、[session.py:9-15](../../raw/v1/app/session.py)
- 另有 `expire_at()` 只读查询到期时间戳，令牌不存在时返回 `None`。
  来源：[auth.py:57-60](../../raw/v1/app/auth.py)

### 4. 续期 —— `touch()` ✅ 已实现

- 令牌不存在 → 返回 `None`。
  来源：[auth.py:65-67](../../raw/v1/app/auth.py)
- **已过期的令牌不续期**：续期前先做 `is_session_valid` 判断，无效则返回 `None`。
  来源：[auth.py:69-70](../../raw/v1/app/auth.py)
- 续期即把 `expires_at` 改写为 `refresh_expiry(now)`（请求时刻 + 30 分钟）。
  来源：[auth.py:71](../../raw/v1/app/auth.py)

### 5. 过期清理 —— `purge_expired()` ✅ 已实现

- 遍历 `_TOKEN_STORE`，用 `is_session_valid` 筛出已过期令牌并 `pop` 掉，返回清理条数。
  来源：[auth.py:75-81](../../raw/v1/app/auth.py)
- 是**主动调用**的清理函数，源码中未见定时任务或自动触发。
  来源：[auth.py:75](../../raw/v1/app/auth.py)（无调度器引用）

### 6. 退出登录 —— `logout()` ❌ 未实现

- 当前函数体为 `raise NotImplementedError("logout is not implemented yet")`。
  来源：[auth.py:84-90](../../raw/v1/app/auth.py)
- docstring 自述 TODO：需要删除 `_TOKEN_STORE` 中的记录，并按
  `docs/testing-guide.md` 补 `test_logout_invalidates` 用例。
  来源：[auth.py:87-88](../../raw/v1/app/auth.py)
- 模块 docstring 同样声明"退出登录功能尚未实现"。
  来源：[auth.py:1-5](../../raw/v1/app/auth.py)
- 完整需求与验收标准见 [`logout-plan.md`](../project/logout-plan.md)。

### 7. 职责划分

- **原文事实（明确声明）**：auth 负责令牌生命周期，session 负责时间判定，"两个文件的职责不要混"。
  来源：[PRD-login-2.5.0.pdf「3. 涉及模块」](../../raw/v1/pdf/PRD-login-2.5.0.pdf)（PDF 第 9-10 行）
- **原文事实**：`app/session.py` 只负责"令牌是否仍然有效"的判断，不负责签发和存储。
  来源：[session.py:1-4](../../raw/v1/app/session.py)
- **【推导】** 上述职责划分与源码实际调用关系**相符**：
  `session.py` 只导出常量与两个纯函数；`auth.py` 持有 `_TOKEN_STORE` 与 `_SECRET`，
  并且是唯一调用 `refresh_expiry` / `is_session_valid` 的模块。
  依据：源码观察第 1-6 条 + [auth.py:14](../../raw/v1/app/auth.py)。
  注：PRD 为**草稿**，此处的"符合"指代码现状与草稿描述一致，不等于草稿已生效。

---

## 配置接线（重点核对项）

**问题：`app/config.py` 的 `SESSION_TTL_MINUTES` 是否接入了有效期计算？**

- **原文事实**：`config.py` 声明 `SESSION_TTL_MINUTES` 是"运维覆盖用的，默认值为空"，
  并说"真正的默认有效期常量在 app/session.py"。
  来源：[config.py:1-5](../../raw/v1/app/config.py)
- **原文事实**：该值来自环境变量，默认空字符串 `""`。
  来源：[config.py:10](../../raw/v1/app/config.py)
- **源码观察**：`raw/v1/app/auth.py` 的**导入语句**只从 `.session` 导入
  `SESSION_TTL_SECONDS, is_session_valid, refresh_expiry`，**没有导入 `config`**。
  来源：[auth.py:14](../../raw/v1/app/auth.py)
- **源码观察**：`raw/v1/app/session.py` **没有** `import config`，也没有读取任何环境变量。
  来源：[session.py:1-20](../../raw/v1/app/session.py)（全文 20 行）
- **源码观察**：有效期计算的两条路径 —— 签发 `refresh_expiry(now)`
  （[auth.py:33](../../raw/v1/app/auth.py)）与续期 `refresh_expiry(now)`
  （[auth.py:71](../../raw/v1/app/auth.py)）—— 都只依赖 `SESSION_TTL_SECONDS`。
- **【推导】在 `raw/v1/app/` 可见的三个模块范围内，`SESSION_TTL_MINUTES`
  没有被接入有效期计算。** 环境变量即使被设置，也不会影响上述两条路径。
  依据：上述五条源码观察。
  ⚠️ **此推导有范围限制**：只能说明*可见的这三份源码*中不存在该调用关系。
  读取 `config` 的代码可能位于未提供的文件中（如应用入口、中间件），
  **不得据此断言"配置一定不生效"**。故本页记为**待核对**，不作为已定结论。

---

## 来源

| 来源 ID | 相对路径 | 快照 SHA-256（前 12 位） | 引用位置 |
| --- | --- | --- | --- |
| SRC-001 | `raw/v1/app/auth.py` | `6f62febaf33b` | L1-5、L14、L16-17、L20-22、L25-35、L38-43、L46-54、L57-60、L63-72、L75-81、L84-90 |
| SRC-003 | `raw/v1/app/session.py` | `9eec7917294a` | L1-4、L9-15、L18-20（全文 20 行） |
| SRC-002 | `raw/v1/app/config.py` | `b8ca51f475a1` | L1-5、L10（全文 10 行） |
| SRC-010 | `raw/v1/pdf/PRD-login-2.5.0.pdf` | `fd22246372d7` | 「3. 涉及模块」（PDF 第 9-10 行） |

> 来源链接从 `wiki/technical/` 发布位置计算（`../../raw/v1/...`）。完整哈希见 `manifests/sources.json`。

## 待核对事项

1. **`SESSION_TTL_MINUTES` 的接线 —— 待核对（重点）。**
   可见三模块中无调用关系，但读取点可能在未提供的文件中。需补充应用入口/中间件源码后复核。
2. **`logout()` 实现状态 —— 需求草稿，尚未实现。**
   v2.5.0 PRD 为**草稿**（`raw/v1/pdf/PRD-login-2.5.0.pdf` 标题自带"草稿"），
   且源码仍为 `NotImplementedError`。**不得把 PRD 的目标当作已完成行为。**
3. **签名密钥硬编码 —— 待核对。** `_SECRET` 明文写死在 `auth.py:17`。
   原文未声明这是临时演示值还是生产配置；是否符合安全要求需人工裁定。
   本页只记录现状，不推断其影响。
4. **`_TOKEN_STORE` 进程内存储 —— 待核对。** 重启进程即丢失全部令牌；
   多实例部署下各实例存储不共享。原文未声明部署形态，故不推断。
5. **`X-Session-Expires-At` 响应头产出位置 —— 待核对。**
   见 [`login-session.md`「待核对事项」第 3 条](./login-session.md)。
6. **`purge_expired()` 的调用者 —— 未知。** 源码中未见调用点，是否有定时任务在未提供的文件中，原文未说明。
