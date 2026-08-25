# DeepSeek Responses API `max_output_tokens` 问题与调查报告

> 核查与实验日期：2026-08-25（Asia/Shanghai）
> 资料范围：官方语义部分仅使用 DeepSeek 官方 API 文档、指南、模型页、更新日志和服务状态页；运行时部分分别使用 OpenAI Python SDK 与 Python 标准库 raw HTTP 调用 DeepSeek 官方 API。

## 问题

需要验证 DeepSeek 官方 Responses API 中 `max_output_tokens` 是否存在不生效或语义实现异常的问题，尤其关注 `Responses API + reasoning + web_search` 场景下，该参数能否限制整个 Response 的累计输出 token。

学员使用 OpenAI Python SDK 请求：

```python
response = client.responses.create(
    model="deepseek-v4-flash",
    input="分析 RKLB 最新基本面和消息面",
    tools=[{"type": "web_search"}],
    max_output_tokens=4096,
    reasoning={"effort": "high"},
)
```

观察到：

```text
request.max_output_tokens = 4096
usage.output_tokens = 6000
usage.output_tokens_details.reasoning_tokens = 2862
status = completed
incomplete_details = null
```

核心疑问是 `max_output_tokens` 是否被忽略、是否漏算 reasoning tokens，或是否在服务端执行 `web_search` 后形成的多轮 reasoning / tool continuation 之间被重置。调查需要通过 reasoning on/off、web search on/off 四组 A/B，并用 raw HTTP 排除 SDK 序列化问题。

## 结论摘要

1. **[已文档化]** DeepSeek Responses API 支持顶层参数 `max_output_tokens`。官方将其定义为“一个 response 中可生成 token 数的上限”，并明确包含**可见输出 token 与 reasoning token**。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)
2. **[已文档化]** `usage.output_tokens` 是 response 的输出 token 总数，`usage.output_tokens_details.reasoning_tokens` 是其中 reasoning token 的明细。因此，按官方字段定义，判断是否越界应比较 `usage.output_tokens` 与 `max_output_tokens`，不应把 reasoning tokens 再加一次。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)
3. **[合同级推断，非明确实现说明]** “in the response”与 response 级 `usage.output_tokens` 共同指向：同一个 `POST /responses` 所产生的输出应共享一个 response 级预算。官方没有写明预算只约束某个 generation stage，也没有写明 server-side `web_search` 后会重置预算。但官方同样**没有明确描述**多段 reasoning/search/message 的内部累计算法，所以不能仅凭文档断言服务端实际实现一定是累计计数。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)
4. **[已文档化]** 达到 `max_output_tokens` 是 response 被截断的示例原因；流式调用的最终事件应为 `response.incomplete`。Response schema 允许 `status: incomplete`，且 `incomplete_details.reason` 可为 `max_output_tokens`。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/)
5. **[已文档化]** `web_search` 是 DeepSeek 服务端执行的内置工具，服务端自动 continuation 最多 10 轮；响应可包含 `web_search_call` item。官方没有给 `web_search` 声明 `max_output_tokens` 豁免、独立预算或逐阶段重置规则。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/)
6. **[条件性判断]** 若一次实际响应同时满足 `usage.output_tokens = 6000`、请求 `max_output_tokens = 4096`、`status = completed`、`incomplete_details = null`，且确认线上的原始请求确实携带该参数，则它与上述官方合同语义明显冲突。仅凭官方资料无法判定内部根因为“参数被整体忽略”“web search 每阶段重置”或“usage 记账错误”；这些都仍需 A/B 和 raw HTTP 证据。

## 实验结果

统一请求 `model=deepseek-v4-flash`、`max_output_tokens=1000`，四组使用同一 NVIDIA 分析 prompt。SDK 为 OpenAI Python SDK `openai>=1,<3`，`base_url=https://api.deepseek.com`；raw HTTP 直接 `POST https://api.deepseek.com/v1/responses`。两种 transport 发送相同 JSON 字段。

### OpenAI Python SDK

| Case | Reasoning | Web Search | Limit | Output Tokens | Reasoning Tokens | Status | Exceeded |
|---|---|---|---:|---:|---:|---|---|
| 1 | none | false | 1000 | 1000 | 0 | incomplete | false |
| 2 | high | false | 1000 | 1000 | 223 | incomplete | false |
| 3 | none | true | 1000 | 2061 | 0 | incomplete | **true** |
| 4 | high | true | 1000 | 5501 | 3927 | incomplete | **true** |

四组的 `incomplete_details` 均为 `{"reason":"max_output_tokens"}`，`error` 均为 `null`。

### Raw HTTP

| Case | Reasoning | Web Search | Limit | Output Tokens | Reasoning Tokens | Status | Exceeded |
|---|---|---|---:|---:|---:|---|---|
| 1 | none | false | 1000 | 1000 | 0 | incomplete | false |
| 2 | high | false | 1000 | 1000 | 1000 | incomplete | false |
| 3 | none | true | 1000 | 1876 | 0 | incomplete | **true** |
| 4 | high | true | 1000 | 1336 | 1135 | incomplete | **true** |

四组的 `incomplete_details` 均为 `{"reason":"max_output_tokens"}`，`error` 均为 `null`。SDK 与 raw HTTP 的具体 token 数不同属于非确定性生成；关键分类完全一致：无工具不超限，启用 `web_search` 后超限。

### Output item 记录

| Transport / Case | Input | Total | Approx visible | Output item types（按返回顺序） |
|---|---:|---:|---:|---|
| SDK / 1 | 58 | 1058 | 1000 | `message` |
| SDK / 2 | 137 | 1137 | 777 | `reasoning, message` |
| SDK / 3 | 86213 | 88274 | 2061 | `message, web_search_call, message, web_search_call, web_search_call, web_search_call, message, web_search_call, message, web_search_call, web_search_call, web_search_call, message, web_search_call, web_search_call, web_search_call, message, web_search_call, message` |
| SDK / 4 | 247538 | 253039 | 1574 | `reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, web_search_call, reasoning, message, web_search_call, reasoning, message, web_search_call, web_search_call, reasoning, message, web_search_call, reasoning` |
| Raw / 1 | 58 | 1058 | 1000 | `message` |
| Raw / 2 | 137 | 1137 | 0 | `reasoning` |
| Raw / 3 | 73966 | 75842 | 1876 | `message, web_search_call, web_search_call, message, web_search_call, web_search_call, web_search_call, message, web_search_call, web_search_call, message, web_search_call, web_search_call, web_search_call, message, web_search_call, message` |
| Raw / 4 | 10112 | 11448 | 201 | `reasoning, message, web_search_call, web_search_call, reasoning` |

Response IDs（供 DeepSeek 支持侧检索）：SDK Case 1–4 分别为 `16bdc68d-a59d-4a83-9cb4-2c123d49eed7`、`51ed087a-7b34-4fc9-a5de-fc132b55282b`、`faa67d31-9572-4daf-a7c7-1410393ae876`、`44bd35ff-b8e7-4614-bca3-21c52e1836c0`；raw Case 1–4 分别为 `078c69a1-9b94-49e2-b0d5-56b1b34005ba`、`63813c37-1ad4-4c36-9528-f89b9781784b`、`385d2f62-65d6-4a1c-ace2-58e2fdcab16a`、`067a8e59-5887-4e5d-9a50-696c2cbd3ef2`。

## Root Cause 推测

证据能支持的最窄结论是：**DeepSeek 服务端在 `web_search` 自动续跑期间，没有以最终 `usage.output_tokens` 的累计口径严格执行 response 级 `max_output_tokens`。**

- Case 1/2 证明参数并非被 Responses API 整体忽略。
- Case 2 证明无工具时 reasoning tokens 被纳入同一个 1000-token 上限。
- Case 3 在 `reasoning=none` 时仍超限，排除了“只漏算 reasoning tokens”这一解释。
- SDK 与 raw HTTP 分类一致，排除了 SDK 序列化导致字段丢失。
- 超限只随 `web_search` 出现，且伴随多个 `message` / `reasoning` / `web_search_call` item，最符合“continuation 阶段的剩余预算未全局递减或被重置”或“limiter 使用单阶段计数、usage 使用累计计数”。

最后两种机制仍是推测。没有 DeepSeek 服务端源码或官方回复，不能断言具体内部实现；也不能仅凭本实验排除最终 usage accounting 本身有误。

## 是否属于 DeepSeek API Bug

**是，现有证据足以报告为 DeepSeek server-side API contract bug。** Raw HTTP 已证明请求中直接携带 `max_output_tokens=1000`，但有工具的响应累计输出分别达到 1876 和 1336。官方把该参数定义为包含 visible 与 reasoning tokens 的 response 输出 upper bound，且没有 web search 豁免。

本次响应正确返回了 `incomplete/max_output_tokens`，所以复现的是“限制触发过晚/累计输出突破上限”。用户原始观察的 `4096 → 6000` 且 `completed/null` 是更严重的状态异常；本轮没有重复该高成本原始条件，因此应在 bug report 中标为先前观察，而不是本轮 raw HTTP 已复验事实。

## 十个问题的直接回答

1. `max_output_tokens` **部分生效**：无工具时严格生效，`web_search` 场景下不能限制整个 response 的累计输出。
2. 无 tool calling 时正常，两种 transport 的 Case 1/2 都是 1000。
3. Reasoning 会改变 token 构成和超限幅度，但不是异常的必要条件。
4. 是；本轮只有启用 `web_search` 的 Case 3/4 突破上限。
5. 否；Case 2 表明 reasoning 被计入限制，Case 3 又表明没有 reasoning 也会超限。
6. “tool loop 每轮重置/未扣减”与证据相符，但只能作为高概率机制假设。
7. 一致；SDK 与 raw HTTP 都得到相同 A/B 分类。
8. 不符合官方 upper-bound 定义。
9. 足以判断为服务端合同违约型 bug，但不足以确定内部代码根因。
10. 可以；仓库中的单文件 MRE 可分别跑 SDK、raw 或两者。

## 最小复现与提交建议

最小复现程序：[`deepseek_max_output_tokens_mre.py`](./deepseek_max_output_tokens_mre.py)。它只从 `DS_API_KEY` 读取密钥，不打印密钥，并记录 request、status、incomplete details、error、usage、每个 output item type、item 计数、visible token 近似值和 `exceeded`。

```bash
export DS_API_KEY='配置在本机环境中，不要写入代码或报告'
python -m pip install 'openai>=1,<3'
python fqa/deepseek-responses-max-output-tokens/deepseek_max_output_tokens_mre.py --transport both --output results.jsonl
```

建议提交给 DeepSeek 的标题：

> Responses API `max_output_tokens` is exceeded during server-side `web_search` loops

建议正文附上本报告的两张实验表、raw Case 3/4 的 response IDs、MRE 文件，并说明：无工具对照严格等于 1000，raw HTTP 的有工具请求分别返回 1876/1336，故问题不依赖 OpenAI SDK。请求官方确认 `web_search` continuation 是否共享 response 级剩余预算，以及 `usage.output_tokens` 和 limiter 是否采用同一计数口径。

## 官方语义逐项核对

| 问题 | 官方资料结论 | 证据等级 | 直接来源 |
|---|---|---|---|
| Responses API 是否支持 `max_output_tokens` | 支持；兼容性表明确列为 `Supported`，API schema 也声明该请求字段 | 已文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| 是否包含 reasoning tokens | 包含 visible output tokens 与 reasoning tokens | 已文档化 | [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| `usage.output_tokens` 与 reasoning 的关系 | `output_tokens` 是输出 token 数；`reasoning_tokens` 是其 breakdown，而不是应额外相加的独立总量 | 已文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| 是否限制整个 Response 的累计输出 | 文案以整个 `response` 为作用域，没有单阶段限定；但未逐字说明 tool loop 的累计算法 | 合同级推断；内部算法未文档化 | [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| `web_search` 是否有特殊预算规则 | 服务端自动 continuation 最多 10 轮；没有预算豁免、重置或独立 ceiling 的说明 | 轮数上限已文档化；token 特殊规则未文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| Function calling 是否有特殊预算规则 | `function` 受支持；文档未声明与 `max_output_tokens` 有关的特殊 accounting | 特殊规则未文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/) |
| 触顶时的 response 状态 | 被截断；流式最终事件示例为 `response.incomplete`，schema 支持 `status: incomplete` | 已文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| 触顶时 `incomplete_details` | `reason` 可为 `max_output_tokens` | 已文档化 | [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| 触顶时 `usage` | `response.incomplete` 携带完整 response object；response schema 中有 `usage` 及 token 明细 | 已文档化到字段存在；具体边界数值未文档化 | [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |
| 触顶时 `error` | `error` 被定义为 response `failed` 时的错误对象；但文档没有逐字规定 token 截断时必须为 `null` | 部分文档化 | [Responses API reference](https://api-docs.deepseek.com/api/create-response/) |

## “整个 Response 累计”能确认到什么程度

官方定义原意可概括为：`max_output_tokens` 是 response 可生成 token 的 upper bound，包含 visible 与 reasoning。官方还把 `usage` 定义为“Token usage statistics for the response”，并将 `reasoning_tokens` 放在 `output_tokens_details` 下。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)

据此可以作出以下分层判断：

- **[已文档化]** 预算不是“只限制可见答案、reasoning 另算”。Reasoning 明确在预算内。
- **[合同级推断]** 一个 API response 的多个 output item 应由 response 级上限覆盖；否则“response 上限”与 response 级 usage 的自然含义无法成立。
- **[未文档化]** DeepSeek 没有公开说明 server-side `web_search` 的每次 model continuation 如何扣减剩余预算，也没有说明 reasoning、tool arguments、`web_search_call.action` 等各类结构分别如何进入底层计数。
- **[未文档化]** 没有任何官方文字支持“每次搜索之后重置 `max_output_tokens`”或“`max_output_tokens` 仅按单阶段生效”。这是可由实验验证的 bug/root-cause 假设，不是官方定义。
- **[已文档化的边界]** DeepSeek Responses API 是无状态的；若客户端 function calling 需要另发一个 `POST /responses` 继续对话，那是新的 response/request，不能把不同 HTTP response 的预算天然合并。这里与一次请求内部由服务端自动执行的 `web_search` 应区分。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)

## `web_search` / tool calling 的官方特殊说明

- **[已文档化]** `function` 与 `web_search` 受支持；`web_search` / `web_search_2025_08_26` 由服务端执行，服务端自动 continuation 最多 10 轮。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)
- **[已文档化]** `web_search_call` 可作为 output item；其 action 可为 `search`、`open_page` 或 `find_in_page`。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)
- **[已文档化]** `search_context_size` 与 `user_location` 会被忽略；顶层 `max_tool_calls` 也会被忽略。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)
- **[已文档化/未文档化边界]** 官方给出 server-side auto-continuation 最多 10 轮，但没有说明“一轮”与实际 `web_search_call` item 数的严格对应关系，也没有说明搜索结果本身是否计入 `input_tokens`、如何计入各 continuation 的上下文，或怎样影响 `max_output_tokens` 的剩余预算。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)
- **[未文档化]** 官方没有针对 `web_search` 声明任何 token-limit 例外。因此不能以“使用了 web search”为由，将 `output_tokens > max_output_tokens` 解释成符合文档的预期行为。

## 达到上限时的预期对象形态

按官方 schema 与事件指南，最稳妥的预期是：

```text
status = incomplete
incomplete_details.reason = max_output_tokens
usage.output_tokens = 本 response 的输出 token 数
usage.output_tokens_details.reasoning_tokens = 上述输出中的 reasoning token 数
```

流式场景的最后事件应为 `response.incomplete`，并携带完整 response object。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)

以下细节官方**没有**说明：

- `usage.output_tokens` 必须恰好等于请求值（而不是略小）；
- tokenizer/停止边界的具体截断粒度（但“upper bound”语义本身不提供 overshoot 豁免）；
- 被截断的最后一个 output item 的精确 `status` 与内容形态；
- incomplete response 的 `error` 是否必须严格为 JSON `null`。

不过，官方把参数定义为 upper bound，因此大幅超过限制、同时返回 `completed` 的行为不能由这些未说明细节合理化。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)

## `deepseek-v4-flash` 的模型限制

- **[已文档化]** 当前别名 `deepseek-v4-flash` 对应 `DeepSeek-V4-Flash-0731`。[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- **[已文档化]** 模型 context length 为 1M，模型级 maximum output 为 384K。[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- **[合同级推断]** 384K 是模型容量上限，不是请求级 `max_output_tokens` 的替代语义；官方没有说明模型容量会把客户端明确传入的 `4096` 自动改写成其他数值。[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/)
- **[已文档化]** 模型同时支持 thinking 与 non-thinking，且默认 thinking。[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- **[已文档化]** Thinking 默认 effort 为 `high`；Responses API 中 `none` 关闭 thinking，`low` 映射 low，`medium` / `high` / `xhigh` 映射 high，`max` 映射 max。官方没有给 reasoning 声明 token-limit 豁免。[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/)
- **[已文档化]** Responses API 支持 `deepseek-v4-flash`。截至核查日，当前 API reference 与模型页还显示 `deepseek-v4-pro`、`deepseek-v4-flash-vision-exp` 也支持 Responses API。[Responses API reference](https://api-docs.deepseek.com/api/create-response/), [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- **[未文档化]** Responses API schema 没有给 `max_output_tokens` 展示默认值、最小值、请求级最大值或“web search 时固定 6000”之类的特殊限制。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)

## 近期变更与文档时效

| 日期 | 官方变更 | 与本问题的关系 | 来源 |
|---|---|---|---|
| 2026-07-31 | V4-Flash 正式版 API 公测；`DeepSeek-V4-Flash-0731` 原生支持 Responses API，并针对 Codex 适配 | 这是当前 flash Responses 行为的主要上线节点 | [Change Log](https://api-docs.deepseek.com/updates/) |
| 2026-08-13 | V4-Pro 正式版上线；官方再次宣布原生 Responses API；V4-Pro 与 V4-Flash 增加 low/high/max 思考强度 | reasoning effort 行为近期发生扩展；更新日志没有提及 `max_output_tokens` accounting 修复或语义变化 | [Change Log](https://api-docs.deepseek.com/updates/) |
| 2026-08-21 | `deepseek-v4-flash-vision-exp` 上线 | 当前 Responses API reference/model page 已将它列为支持模型；与纯文本 flash 的 4096/6000 异常无直接已知关系 | [Change Log](https://api-docs.deepseek.com/updates/) |

**[时效说明]** 截至核查日，当前 API reference、Responses API guide 的兼容性表与 Models & Pricing 均已列出 `deepseek-v4-flash`、`deepseek-v4-pro`、`deepseek-v4-flash-vision-exp`；搜索引擎缓存中仍可能出现“仅支持 Flash、Pro 将于 8 月初支持”的旧版摘要，不应当作 2026-08-25 的现状。[Responses API guide](https://api-docs.deepseek.com/guides/responses_api/), [Responses API reference](https://api-docs.deepseek.com/api/create-response/), [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/), [Change Log](https://api-docs.deepseek.com/updates/)

截至 2026-08-25，官方更新日志没有列出专门针对 `max_output_tokens`、server-side web search token accounting、或超限仍 `completed` 的修复/已知问题条目。[Change Log](https://api-docs.deepseek.com/updates/)

官方状态页在核查时显示整体服务与 API operational，未解决 incident 为空；其公开 incident 列表未发现标题或更新正文命中 `max_output_tokens`、Responses API 或 `web_search` 的相关事件。这只能说明官方未通过状态页披露该字段级问题，不能排除语义实现 bug。[Status summary API](https://deepseek.statuspage.io/api/v2/summary.json), [Status incidents API](https://deepseek.statuspage.io/api/v2/incidents.json)

## 对已观察异常的官方资料判定

用户提供的观察值（本次未复验）为：

```text
request.max_output_tokens = 4096
usage.output_tokens = 6000
usage.output_tokens_details.reasoning_tokens = 2862
status = completed
incomplete_details = null
```

其中 `reasoning_tokens = 2862` 是 `output_tokens = 6000` 的子集，近似可见输出应计算为 `6000 - 2862 = 3138`；不能计算成 `6000 + 2862`。[Responses API reference](https://api-docs.deepseek.com/api/create-response/)

若 raw request 和 raw response 证据确认上述值来自同一个 `POST /v1/responses`：

- **[可依据合同判断]** `6000 > 4096` 与“upper bound 且包含 reasoning”的官方定义不一致；
- **[可依据状态语义判断]** 若确因预算触顶，`completed` / `incomplete_details = null` 也与官方描述的 incomplete 路径不一致；
- **[仍不能仅凭文档判断]** 是执行侧没有传递/读取参数、web search continuation 重置预算、usage 跨阶段累计但 limiter 单阶段计数、还是返回字段记账错误；
- **[仍需实验确认]** Python SDK 是否确实序列化该字段、raw HTTP 是否复现、无工具与有工具是否分化、reasoning on/off 是否分化。

因此，当前最准确的表述是：**该观察若可稳定复现并排除客户端传参问题，足以构成与 DeepSeek 官方 Responses API 契约不一致的 server-side bug 证据；但现有官方资料不足以确认具体 root cause。**

## 官方资料未回答的问题

1. server-side `web_search` 的每次 continuation 是否共享、扣减同一剩余 token budget；
2. tool call arguments、搜索 action 元数据与内部搜索结果分别如何参与 token accounting；
3. budget enforcement 与最终 `usage.output_tokens` 是否使用完全相同的计数口径；
4. 达到限制时最后一个 item 的截断粒度，以及是否允许任何边界 overshoot；
5. 是否已有未公开、尚未进入 changelog 的已知问题或灰度修复。

这些空白只能通过受控 A/B、原始 HTTP 证据或 DeepSeek 官方支持回复补足，不能从现有公开文档中推导为事实。

## 官方来源索引

- [DeepSeek Responses API reference](https://api-docs.deepseek.com/api/create-response/)
- [DeepSeek Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)
- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Change Log](https://api-docs.deepseek.com/updates/)
- [DeepSeek Service Status](https://status.deepseek.com/)
