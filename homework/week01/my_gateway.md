# Mini LLM Gateway

## 目标
实现一个 mini LLM 网关，可对外提供统一的入口用于访问不同的模型。本项目只实现网关本身：调用方（Agent Harness 中的 Loop / Run 层）不在实现范围内。

## 业务约束

### 架构总览：Gateway 内部五层
1. 接口层：FastAPI 入口、Pydantic 协议、统一错误 envelope
2. 治理层：模型白名单、Prompt 模板治理、限流
3. 执行层：Provider Adapter、重试/fallback、流式转发
4. 出口校验层：Structured Output 双层校验、修复边界
5. 可观测层：CallTrace 与 /trace 查询

### 第 1 层：接口层（API / 协议）
- FastAPI 三个入口：POST /chat 普通调用、POST /stream 流式调用、GET /trace Trace 查询（列表 + /{call_id} 详情）
- Pydantic 入口校验：定义自有请求协议，上层 agent 提交：平台模型名、模板选择（name+version）、消息、业务 Schema
- 统一错误 envelope：HTTP 层错误一律返回 `{error: {code, message, call_id}}`；状态码按类别映射：请求问题 4xx、限流 429、上游/网关失败 5xx。错误码本身即协议（unknown_model 等，见验收标准）
- 流式例外：/stream 首块已发出后的失败不再适用 HTTP envelope，以 response.failed 事件结束流
- 附带一个最小示例调用方（业务 Agent）：只通过网关 HTTP 接口访问模型，用于验证统一协议（见验收 10）

### 第 2 层：治理层

#### 模型白名单与能力校验
- 平台逻辑模型名白名单，只有白名单内的模型可被请求
- validate_model 保证 fallback 只在能力等价的模型间发生

#### Prompt 模板治理（名称+版本）
- 模板注册表内置：name + version → 模板内容与 hash；至少包含一个多版本模板和一个带条件分支的模板（供验收与渲染测试）
- 版本可追溯：每次调用的 Trace 中保留实际使用的 Prompt 名称、版本号和 hash，确保行为可回放
- 受限模板渲染：不使用 eval 或 exec，不支持任意 Python 表达式；只允许变量替换和条件分支，模板引擎在沙箱中运行
- 变量校验：渲染前用 Schema 校验输入变量，缺失或超长变量直接拒绝，不进入上游请求

#### 限流
- 调用方以请求头 X-Caller-Id 标识（缺省 default）
- 每调用方限流：窗口内超过配额（次/分钟，配额从配置读入，测试可注入小值）直接 429 + Retry-After，请求不进入执行层，记入 Trace
- 实现用内存计数器即可（单实例；多实例部署超出本次范围）
- 边界区分：限流器产生的 429 是接口层拒绝，不计 attempts、不触发重试；上游返回的 429 属于执行层 is_retryable 白名单

### 第 3 层：执行层（Provider Adapter 与重试/fallback）
- OpenAI Compatible Provider：Base URL、模型名、API Key、Structured Output 能力统一管理
- 数据流：Pydantic Message → Provider → Chat Completions → str + Usage
- 红线：SDK 的 completion 对象不离开 Provider；Gateway 其余部分只消费 str 和 Usage，不与供应商响应类型耦合
- Provider 通过依赖注入接入（应用工厂或依赖覆盖），生产用真实 Provider，测试注入 FakeProvider
- Provider 建客户端时 max_retries=0，关闭 SDK 隐式重试；重试只有 Gateway 一层说了算
- is_retryable 是白名单：连接错误/超时/上游 429 才重试
- 重试与 fallback 次数契约：恰好 1 次重试 + 至多 1 次 fallback；fallback 仅在能力等价的备用模型间发生；fallback 再失败返回明确错误码，不得继续换模型
- fallback 时点红线：仅首 Token（首 chunk）发出前可 fallback；首块发出后失败则结束流并产生 response.failed，不能换模型拼接重写
- Streaming 流式转发：逐块转发；/stream 返回 text/event-stream，事件携带类型与数据（如 content.delta、response.failed）

### 第 4 层：出口校验层（Structured Output）
- 双层校验：第一层请求时带 JSON Schema；第二层返回后用 Pydantic 本地校验。供应商的 Schema 保证不等于应用层安全
- 红线：未通过 Schema 校验的输出不得作为成功响应返回给调用方（对应不变量 1）
- Structured Streaming 边界：流式 JSON 增量解析，不等全部 chunk 到齐；部分 JSON 无法做 Schema 校验，流结束后做最终校验
- 修复有边界：JSON 提取与修复合计至多 1 轮；失败返回 schema_validation_failed，不得静默吞掉，不得无限重试

### 第 5 层：可观测层（Trace）
- 每次调用（成功和失败）记录：call_id、model_used（主/备）、attempts、status、错误码、prompt name+version+hash、输入/输出 Token、Cost、Latency（请求发出到响应完成的耗时）
- Cost = 模型定价 × Token；定价表随模型白名单一起维护（每个逻辑模型 in/out 单价）
- model_used 必须记录实际服务请求的模型，它是不变量 3 唯一可观测的证据

### 测试与离线约束
- 项目提供 requirements.txt（fastapi、httpx、pydantic、openai、pytest）；先 `pip install -r requirements.txt`（本机缺 fastapi/httpx）
- 所有测试离线可运行：FakeProvider（可脚本化控制失败类型、chunk 数与内容、非法 JSON）+ httpx.ASGITransport 直调 FastAPI app；禁止任何真实网络请求
- 验收 = `pytest` 全绿

## 需要修改
1. 在 C:\baidunetdiskdownload\STUDY\Agent\repository\ai-agent-fullstack-training\homework\week01 目录下创建完成项目
2. 不要修改其他路径下的代码
3. git diff 除 homework\week01 路径下的新增文件外，无其他改动

## 验收标准
1. 正常文本请求返回 LLMResponse
2. 未知模型返回 unknown_model
3. 模板版本不存在时返回 unknown_prompt_template
4. 模板变量缺失时返回 missing_prompt_variable
5. 合法 JSON 但不符合 Schema 时返回 schema_validation_failed
6. 主模型连接失败时：恰好 1 次重试 → fallback 到 1 个能力等价的备用模型成功；备用模型也失败时返回明确错误码，不再换模型
7. FakeProvider 分 k 块发送时，/stream 逐块产生 content.delta，delta 数量与顺序和 k 一致
8. 流开始后失败时产生 response.failed，不能静默换模型重写
9. 成功和失败调用都能在 Trace 中找到，字段包含 model_used、attempts、status、输入/输出 Token、Cost、Latency、prompt name+version+hash
10. 示例调用方及其测试代码中不出现供应商 API Key 和 Base URL
11. 同一 X-Caller-Id 超过配额的请求返回 429 + Retry-After，记入 Trace，且不产生重试
12. `pip install -r requirements.txt` 后 `pytest` 全绿，全程无真实网络请求

## 三条不变量（跨项检查，与验收条目相互印证）
- 不变量 1：未通过 Schema 校验的输出不能进入 Agent Loop
- 不变量 2：流已经输出首块以后，不能切换模型重新生成
- 不变量 3：无论使用主模型还是备用模型，上层只依赖同一份请求、响应和错误协议
