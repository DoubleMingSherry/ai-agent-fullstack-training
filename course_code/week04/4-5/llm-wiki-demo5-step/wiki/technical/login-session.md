# 登录会话（有效期、判定、续期）

## 页面身份

- **页面路径（发布后）**：`wiki/technical/login-session.md`
- **草稿路径（当前）**：`staging/technical/login-session.md`
- **主题**：login_demo 登录会话的有效期长度、到期判定、续期方式与测试入口
- **来源快照**：`raw/v1`（快照号 v1，与任何资料版本无关）
- **生成日期**：2026-10-09

## 草稿状态

**草稿 / 未检查。** 未发布到 `wiki/`。按 schema 第 2 条，进入 `wiki/` 前必须经过检查。
本页结论均为对来源的转述或标注来源的推导，未做任何运行时验证。

## 适用范围

- 仅适用 **login_demo 登录服务**的登录会话。
- **不适用**：支付会话（pay_gateway）、匿名浏览会话、记住我令牌。见 `session-types.md`。
- 来源明确声明："本节规则只约束登录会话，不适用于支付会话、匿名浏览会话。"
  （`raw/v1/docs/session-rules.md`「不适用范围」）

---

## 关键结论

### 原文事实

1. **登录会话有效期为 30 分钟，从签发时刻开始计算。**
   来源：[session-rules.md「有效期长度」](../../raw/v1/docs/session-rules.md)
   （该节三条：30 分钟、从签发时刻起算、空闲会话不自动续期。）
   - **改长有效期需要产品确认**，且修改前先读 `docs/session-rules.md`，"不要直接改代码常量"。
     来源：[faq.md「能否把有效期改长一点」](../../raw/v1/docs/faq.md)

2. **到期边界：`now == expires_at` 属于已过期；有效条件写作 `now < expires_at`，不得写 `<=`。**
   来源：[session-rules.md「到期边界」](../../raw/v1/docs/session-rules.md)
   - **该结论"对登录会话长期有效"**，是 2026-09-12 故障记录的明确结论。
     来源：[2026-09-12-expiry-bug.md「结论」](../../raw/v1/history/2026-09-12-expiry-bug.md)

3. **续期由用户请求触发，在响应返回前刷新 `expires_at`；新到期时间 = 请求到达时刻 + 30 分钟。**
   来源：[session-rules.md「续期」](../../raw/v1/docs/session-rules.md)

4. **测试统一入口是 `python -m pytest tests/test_session.py -q`，且运行前需激活虚拟环境。**
   来源：[testing-guide.md「测试入口」](../../raw/v1/docs/testing-guide.md)
   （同节说明：CI 只认 `python -m pytest` 形式，不接受裸 `pytest`。）
   - **排查线索**：若用户反馈"登录状态老是掉"，先确认有效期配置是否被改过（默认 30 分钟），
     再看是否恰好卡在到期时刻被判定为无效（检查边界用例），最后确认前端是否在响应里正确保存了新令牌。
     来源：[faq.md「用户反馈"登录状态老是掉"」](../../raw/v1/docs/faq.md)

5. **与登录会话相关的关键用例名有三个：`test_expire_boundary`、`test_refresh_extends_ttl`、`test_logout_invalidates`。**
   来源：[testing-guide.md「关键用例」](../../raw/v1/docs/testing-guide.md)
   其中 `test_expire_boundary` 期望 `now == expires_at` 返回 `False`。

### 源码观察

6. **有效期常量定义在 `app/session.py`：`SESSION_TTL_SECONDS = 30 * 60`（30 分钟）。**
   来源：[session.py:6](../../raw/v1/app/session.py)

7. **有效条件实现为 `now < expires_at`，与原文事实第 2 条一致。**
   来源：[session.py:15](../../raw/v1/app/session.py)（函数 `is_session_valid`）

8. **续期实现为 `now + SESSION_TTL_SECONDS`，即"请求时刻 + 30 分钟"，与原文事实第 3 条一致。**
   来源：[session.py:20](../../raw/v1/app/session.py)（函数 `refresh_expiry`）

9. **`app/session.py` 自身不签发、不存储令牌，只负责"令牌是否仍然有效"的时间判断。**
   来源：[session.py:1-4](../../raw/v1/app/session.py)（模块 docstring）

10. **`app/auth.py` 是签发与续期的实际调用方：导入并使用 `SESSION_TTL_SECONDS`、`is_session_valid`、`refresh_expiry`。**
    来源：[auth.py:14](../../raw/v1/app/auth.py)
    续期入口是 `touch()`：[auth.py:63-72](../../raw/v1/app/auth.py)

### 历史记录

11. **2026-09-12 故障：到期那一刻仍可访问。**
    现象为令牌剩 0 秒时接口仍返回 200；排查发现判断写成了 `now <= expires_at`；
    修复为把 `<=` 改成 `<` 并补边界用例。
    来源：[2026-09-12-expiry-bug.md「现象 / 排查 / 修复」](../../raw/v1/history/2026-09-12-expiry-bug.md)
    ⚠️ 该记录描述的是**当时**的排查与修复过程，**不是**当前实现；当前实现以源码观察第 7 条为准。

12. **v2.4.0 发布（2026-09-20）把到期时刻改为判定为无效，并维持 30 分钟不变。**
    来源：[release-notes-2.4.0.html「变更内容」](../../raw/v1/web/release-notes-2.4.0.html)
    - **升级注意**：依赖旧行为的调用方需要先确认 —— "凡是把到期时刻当成有效的客户端逻辑，都要改成无效。"
      来源：[release-notes-2.4.0.html「升级注意」](../../raw/v1/web/release-notes-2.4.0.html)

13. **v2.4.0 新增响应头 `X-Session-Expires-At`，返回续期后的到期时间戳。**
    来源：[release-notes-2.4.0.html「变更内容」第 3 条](../../raw/v1/web/release-notes-2.4.0.html)

### 推导

14. **【推导】文档规则、源码实现与历史修复三者对"到期边界"的口径一致**，即 `now == expires_at` 为无效。
    依据：原文事实第 2 条 + 源码观察第 7 条 + 历史记录第 11 条。
    ⚠️ 一致仅指三份文本互相印证，**不等于**该边界已被实际测试通过——本次未运行测试（见待核对第 1 条）。

---

## 来源

| 来源 ID | 相对路径 | 快照 SHA-256（前 12 位） | 引用位置 |
| --- | --- | --- | --- |
| SRC-003 | `raw/v1/app/session.py` | `9eec7917294a` | L1-4、L6、L9-15、L18-20 |
| SRC-001 | `raw/v1/app/auth.py` | `6f62febaf33b` | L14、L63-72 |
| SRC-006 | `raw/v1/docs/session-rules.md` | `bfa3dbaa5c7b` | 「有效期长度」「到期边界」「续期」「不适用范围」 |
| SRC-007 | `raw/v1/docs/testing-guide.md` | `524507aed1aa` | 「测试入口」「关键用例」 |
| SRC-008 | `raw/v1/history/2026-09-12-expiry-bug.md` | `776a7b569663` | 「现象」「排查」「修复」 |
| SRC-012 | `raw/v1/web/release-notes-2.4.0.html` | `430eb87bfe96` | 「变更内容」 |
| SRC-004 | `raw/v1/docs/faq.md` | `c28750172fb9` | 「用户反馈"登录状态老是掉"」「能否把有效期改长一点」 |

> 来源链接从 `wiki/technical/` 发布位置计算（`../../raw/v1/...`）。完整哈希见 `manifests/sources.json`。

## 待核对事项

1. **测试是否实际通过 —— 未知。** 原文只列出用例名与验收要求，**不含本次执行结果**。
   历史记录自述曾运行通过（`raw/v1/history/2026-09-12-expiry-bug.md`「修复」），
   但那是**历史自述**，本次**未运行任何测试**。不得把"验收要求"读作"已经通过"。
2. **`SESSION_TTL_MINUTES` 是否接入有效期计算 —— 待核对。** 见
   [`token-lifecycle.md`「待核对事项」](./token-lifecycle.md)。
3. **`X-Session-Expires-At` 响应头是否真的已由当前代码产出 —— 待核对。**
   `raw/v1/web/release-notes-2.4.0.html` 称已新增，但 `raw/v1/app/` 下可见的三个模块中
   没有产生该响应头的代码；产出位置可能在未提供的文件中。**不推断为"不存在"。**
4. **"管理后台"与"登录服务"的关系 —— 未知。** `session-rules.md`「有效期长度」写的是
   "管理后台的登录会话"，是否所有登录会话共用 30 分钟，原文未说明。
5. **30 分钟是否可被运维配置覆盖 —— 未知。** FAQ 称"默认 30 分钟"，
   `config.py` 提供覆盖入口，但见第 2 条；当前文档未声明覆盖后的实际取值。
