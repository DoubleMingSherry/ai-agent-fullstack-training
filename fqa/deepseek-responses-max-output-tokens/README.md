# DeepSeek Responses API `max_output_tokens` 异常

> 调查日期：2026-08-25

## 问题

在 DeepSeek 官方 Responses API 中使用 `reasoning + web_search` 时，`max_output_tokens` 似乎无法限制整个 Response 的累计输出 token。

学员观察到：

```text
model = deepseek-v4-flash
max_output_tokens = 4096
reasoning.effort = high
web_search = enabled

usage.output_tokens = 6000
usage.output_tokens_details.reasoning_tokens = 2862
status = completed
incomplete_details = null
```

核心问题：这是 SDK 传参问题、reasoning token 记账问题，还是 DeepSeek 服务端在 `web_search` 多轮执行时没有正确实施累计 token 上限？

## 结论

**这是 DeepSeek Responses API 的服务端异常，触发条件与 `web_search` 明确相关。**

- 无 `web_search` 时，`max_output_tokens=1000` 能严格限制输出。
- 启用 `web_search` 后，无论 reasoning 是否开启，累计输出都会突破 1000。
- Reasoning tokens 正常包含在 `usage.output_tokens` 和上限内，不是单独漏算 reasoning 导致的。
- OpenAI Python SDK 和 raw HTTP 都能复现，排除 SDK 序列化问题。
- 官方将 `max_output_tokens` 定义为整个 Response 的输出上限，包含 visible 与 reasoning tokens；没有为 `web_search` 声明例外。

因此，当前证据足以作为 DeepSeek server-side API bug 提交。最可能的原因是服务端在 web-search continuation 之间没有共享或正确扣减剩余 token budget，但具体实现仍需 DeepSeek 官方确认。

## 核心实验

统一使用 `deepseek-v4-flash`、相同 prompt 和 `max_output_tokens=1000`。

### OpenAI Python SDK

| Case | Reasoning | Web Search | Output Tokens | Reasoning Tokens | Status | Exceeded |
|---|---|---|---:|---:|---|---|
| 1 | none | false | 1000 | 0 | incomplete | false |
| 2 | high | false | 1000 | 223 | incomplete | false |
| 3 | none | true | 2061 | 0 | incomplete | **true** |
| 4 | high | true | 5501 | 3927 | incomplete | **true** |

### Raw HTTP

| Case | Reasoning | Web Search | Output Tokens | Reasoning Tokens | Status | Exceeded |
|---|---|---|---:|---:|---|---|
| 1 | none | false | 1000 | 0 | incomplete | false |
| 2 | high | false | 1000 | 1000 | incomplete | false |
| 3 | none | true | 1876 | 0 | incomplete | **true** |
| 4 | high | true | 1336 | 1135 | incomplete | **true** |

本轮响应均返回 `incomplete_details.reason=max_output_tokens`。这说明限制并非完全失效，而是在 `web_search` 多轮执行中触发过晚，最终累计输出已经超过上限。

SDK 与 raw HTTP 的具体数值不同是模型生成的非确定性所致，但 A/B 结果完全一致。

供 DeepSeek 支持侧检索的 raw HTTP Response IDs：

- Case 3：`385d2f62-65d6-4a1c-ace2-58e2fdcab16a`
- Case 4：`067a8e59-5887-4e5d-9a50-696c2cbd3ef2`

## 官方语义

DeepSeek 官方文档规定：

- Responses API 支持 `max_output_tokens`。
- 它是一个 Response 可生成 token 的上限。
- 上限包含 visible output tokens 和 reasoning tokens。
- 达到上限时应返回 `status=incomplete` 和 `incomplete_details.reason=max_output_tokens`。
- `web_search` 没有独立预算或突破上限的例外说明。

所以 `output_tokens > max_output_tokens` 不符合官方定义。

## 最小复现

复现程序：[deepseek_max_output_tokens_mre.py](./deepseek_max_output_tokens_mre.py)

```bash
export DS_API_KEY='your-key'
python -m pip install 'openai>=1,<3'
python fqa/deepseek-responses-max-output-tokens/deepseek_max_output_tokens_mre.py \
  --transport both \
  --output results.jsonl
```

程序会执行四组 A/B，记录 request、status、usage、output item 类型和 `exceeded`。API Key 只从 `DS_API_KEY` 读取，不会写入输出。

## Bug Report 建议

标题：

> Responses API `max_output_tokens` is exceeded during server-side `web_search` loops

提交时附上两张实验表、raw HTTP Response IDs 和本目录的 MRE，并请 DeepSeek 确认 web-search continuation 是否共享 Response 级剩余 token budget。

## 官方来源

- [Responses API reference](https://api-docs.deepseek.com/api/create-response/)
- [Responses API guide](https://api-docs.deepseek.com/guides/responses_api/)
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [Change Log](https://api-docs.deepseek.com/updates/)
