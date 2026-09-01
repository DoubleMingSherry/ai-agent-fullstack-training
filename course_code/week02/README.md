# Week 02

| 小节 | 主题 | 主要内容 |
| --- | --- | --- |
| [2-1](./2-1/) | Function Calling 与 Tool Use | 建立“模型提议、Runtime 执行”的职责边界，使用 Input、Output、Error Schema 定义工具协议，并实现工具选择、权限与风险控制、超时重试、审计 Trace、Tool Result 回传和插件化 Harness。 |
| [2-2](./2-2/) | Tool Runtime 设计：让 Tool Call 进入受控执行 | 区分 ToolDefinition、Registry、ToolSnapshot 与 Runtime 的职责，通过注册、发现和不可变快照保证模型所见与实际执行一致，并以 prepare、execute、finalize 三阶段完成参数校验、权限与依赖检查、超时重试、结果封装及审计。 |
| [2-3](./2-3/) | MCP：外部能力接入 | 区分 Host、Client、Server 与 Agent Loop 的职责，理解 JSON-RPC、初始化及 Tool、Resource、Prompt 语义，通过 stdio 或 Streamable HTTP 发现能力、限定工具名称，并用 Adapter 将远端结果归一化后接入现有 Runtime。 |
| [2-4](./2-4/) | 工具治理与安全边界 | 按工具分级、白名单、RBAC、人工确认和业务预检查固定执行边界，结合参数校验、副作用感知的超时重试、调用编排、结果脱敏与决策/执行审计，构建可测试、可追踪的统一 Runtime 入口。 |
| [2-5](./2-5/) | Agent 工具调用基础设施 v2 | 将 Gateway、工具注册表、命名投影和能力快照接入 Agent，以 Schema 双边校验、固定策略顺序、可恢复审批、硬超时与有限重试治理本地及 MCP 工具，并通过 Audit、Trace 和 LoopGuard 形成可验证的执行链。 |
