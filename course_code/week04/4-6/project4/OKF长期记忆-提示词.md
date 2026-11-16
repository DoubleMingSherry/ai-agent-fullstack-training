# 提示词：为 project3 Codebase Agent 增加基于 OKF 的长期记忆

> 用法：把下面整段（从「## 角色」到「## 验收清单」）原样交给一个能读代码、改代码、跑测试的编码 Agent。
> 本机已安装 `pi-okf@0.4.0`（OKF v0.2）。落地目录：`project3/v2/`。

---

## 角色

你是 `project3/v2` 这个**受治理 Codebase Agent** 的维护者。项目由三块构成：
pi 扩展点（`src/pi/*`）、工具运行时（`src/runtime.ts` + `src/registry.ts` + `src/policy.ts`）、
以及十件仓库工具（`src/tools/*`）。每一次 Run 都是无状态的：Agent 读过的代码、得出的结论，
Run 一结束就全没了，下一趟还得从头再读一遍。

## 目标

给这个 Agent 增加**长期记忆**：跨 Run 记住关于代码库的「概念」，并且把记住的内容
**落成 Open Knowledge Format（OKF v0.2）文档**，下一趟开工前自动回读，而不是只靠模型主动想起。

一句话验收：**同一份记忆 bundle，Run A 写进去、Run B 不重新读代码也能取回来。**

## 必须交付的六件事

1. **配置**
   - 在 `config/platform.yaml` 增加 `memory` 段：`enabled`、`bundle`（bundle 根目录，相对 `config/` 解析）、
     `recall_limit`、`recall_chars`、`require_confirmation`。
   - 在 `src/config.ts` 把这一段解析进 `PlatformConfig.memory`（字段名 camelCase，缺省值要能跑）。
   - 记忆 bundle 默认放在仓库工作区之外（例如 `../memory/okf`），它不属于 `sandbox.root` 管的那个仓库。

2. **记忆模块 `src/memory.ts`**
   - 概念 ID = bundle 内相对路径去掉 `.md`；概念文件必须带 YAML frontmatter，且 `type` 非空（OKF 硬要求）。
   - 提供 `list()`、`search(query)`、`read(id)`、`remember(input)`、`digest(limit, chars)`、`validate()`。
   - `remember` 写入或更新概念，并同步维护 bundle 根 `index.md`（含 `okf_version: "0.2"` 与分组链接）
     和 `log.md`（`YYYY-MM-DD` 日期分组、新在前、每组至少一条列表项）。
   - 所有路径必须限制在 bundle 内，拒绝 `..` 穿越与绝对路径。
   - frontmatter 建议字段：`type`、`title`、`description`、`tags`、`generated: { by, at }`；
     可选 `resource`、`status`、`sources`。`generated.by` 用 OKF actor 约定（例如 `codebase-agent/2.0.0`）。

3. **两件受治理的工具 `src/tools/memory.ts`**
   - `memory_recall`（risk=read）：给 `query` 就检索，给 `id` 就返回该概念全文；都没有就列出已有概念摘要。
     返回里必须带概念 ID，方便下一步按 id 读全文。
   - `memory_remember`（risk=write）：按 OKF frontmatter 写入/更新一个概念。
   - 两件工具都要注册进 `ToolRegistry`、写进 `config/platform.yaml` 的
     `policy.agent_tool_allowlist` 与 `policy.role_permissions`（`memory:read` / `memory:write`），
     并在 `src/contract.ts` 里登记语义规则（自检要求每件注册工具都有一条路径，哪怕是空数组）。
   - 调用必须走 `ToolRuntime`，照常落审计、照常受 `gate` 与审批约束，不许绕过治理层。

4. **开跑前自动回读（检索注入）**
   - `renderSystemPrompt` 在系统提示词末尾追加一段「长期记忆（OKF）」，内容来自记忆模块的 `digest`。
   - 摘要只放「标题 + 概念 ID + 一句描述」，不塞全文；全文让模型用 `memory_recall id=...` 按需取。
   - 回读结果要能被记账（Trace 里留一条 `memory.recalled`，说清注入了几个概念、多少字符）。

5. **提示词规则**
   - 在 `config/prompts.yaml` 的 `codebase.agent` 模板里补一段长期记忆使用规则：
     什么时候 `memory_recall`、什么时候 `memory_remember`、OKF 字段要求、
     「与当前代码冲突时以代码为准并更新记忆」、「不许把没读到的东西写进记忆」。

6. **测试 `tests/unit/kg_memory.test.ts`**
   - 至少覆盖：remember 落盘是合法 OKF 概念（frontmatter 有 `type`）、`index.md`/`log.md` 同步；
     recall 能按 `query` 与 `id` 取回；`..` 穿越/空 `type` 被拒；
     系统提示词里包含长期记忆摘要；**跨会话回读**（同一 bundle，新 platform 实例能取回上一个会话写的概念）。
   - 不许改样本仓库，不许把现有 121 个单测改红。

## 约束

- 遵守 OKF v0.2：`type` 必需；`index.md`/`log.md` 是保留名；跨概念链接用 bundle 相对路径（`/concepts/x.md`）；
  日志日期 `YYYY-MM-DD` 且新在前。
- 不破坏现有单测；`npm test` 必须全绿。
- 不编造业务规则：写进记忆的内容必须来自 Agent 实际读到的代码或明确的事实。
- 保持现有代码风格：中文注释、错误码用大写下划线、每个新工具都要有完整 `description` 与参数边界
  （`limit` 要同时写下界和上界，`additionalProperties: false`）。

## 验收清单

1. `npm test` 121+ 全绿。
2. `node --import tsx src/cli.ts invoke memory_remember '{...}'` 经 `approvals decide` + `approvals execute`
   后，`memory/okf/` 下出现概念文件，`index.md` 与 `log.md` 同步更新。
3. `node --import tsx src/cli.ts invoke memory_recall '{"query":"..."}'` 能把刚写的概念取回来。
4. 用 `pi-okf` 校验：`node <pi-okf>/bin/pi-okf.js validate memory/okf` 退出码 0（或仅 soft warning），
   无 hard error。
5. 新旧代码对比：`git status`/文件清单能说清只加了哪些文件、改了哪几处。



## 测试命令
# 1) 记住一个概念（写需要审批）
BODY=$(printf '# 调用链\n路由→Service→仓储（内存假库）。')
node --import tsx src/cli.ts invoke memory_remember "$(jq -nc --arg b "$BODY" '{id:"concepts/login-flow",type:"Code
 Concept",title:"登录调用链",description:"从路由到假数据库的登录主链",tags:["auth"],body:$b}')"

node --import tsx src/cli.ts approvals decide <approvalId> --decision approve --actor trainer-1 --roles maintainer

node --import tsx src/cli.ts approvals execute <approvalId>

# 2) 新进程回读（＝新 Run）
node --import tsx src/cli.ts invoke memory_recall '{"query":"登录"}'


# 3) 全量测试
npm test
