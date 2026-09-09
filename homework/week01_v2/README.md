# Mini LLM Gateway v2 — 统一模型调用服务（双协议）

一个极简 LLM 网关：通过**适配器模式**封装两种供应商协议，按请求中的
`model` 字段动态路由，屏蔽底层鉴权、请求体结构与返回格式差异，并提供模板治理、
限流、重试/fallback、Structured Output 双层校验与完整调用可观测（Trace）。
本项目只实现网关本身（调用方 Agent Harness 的 Loop / Run 层不在范围内），
另附一个最小示例调用方 `examples/mini_agent.py`。

| 逻辑模型 | 协议 | 适配器 | 能力集合 |
| --- | --- | --- | --- |
| `deepseek-v4-pro` | OpenAI **Responses API** | `ResponsesApiAdapter` | text + json_schema + stream |
| `deepseek-v4-flash` | Anthropic **Messages API** | `MessagesApiAdapter` | text + json_schema + stream |

> 两条协议的所有差异只允许封装在各自适配器内部；红线：任一协议的原生 SDK 对象
> （completion / response / event）绝不离开适配器。网关其余部分只消费
> 统一的 `str` + `TokenUsage` 与统一流块。

## 架构总览（网关内部五层）

```
接口层    FastAPI 入口（/chat /stream /trace）、Pydantic 协议、统一错误 envelope
治理层    模型白名单与动态路由、Prompt 模板治理（name+version+hash、沙箱渲染）、限流
执行层    双协议适配器、指数退避重试(≤3/模型)与能力等价 fallback、流式转发
出口校验   Structured Output 双层校验（请求时交给适配器 + 返回后本地 Pydantic 校验）、单轮修复
可观测    CallTrace（Token 分类统计 + Cost + Latency + TTFT）与 /trace 查询
```

## 目录结构

```
week01_v2/
├── gateway/                     # 网关实现
│   ├── app.py                   # 第1层：FastAPI 工厂（依赖注入 providers）
│   ├── models.py                # 第1层：请求/响应 Pydantic 协议（LLMResponse…）
│   ├── errors.py                # 第1层：错误码 -> HTTP 状态 / 统一 envelope
│   ├── registry.py              # 第2层：模型白名单/能力/定价/fallback 声明
│   ├── templates.py             # 第2层：模板注册表 + 受限沙箱渲染器
│   ├── ratelimit.py             # 第2层：按逻辑模型独立限流（窗口 429 + Retry-After）
│   ├── providers/               # 第3层：统一 Provider 协议 + 双协议真实适配器
│   ├── service.py               # 治理/执行/出口/可观测编排（重试、fallback、流式）
│   ├── structured.py            # 第4层：出口 JSON Schema 本地校验 + 单轮修复
│   ├── trace.py                 # 第5层：CallTrace / TraceStore
│   └── config.py                # 凭证只从环境变量读取
├── examples/mini_agent.py       # 最小示例调用方（只走网关 HTTP 协议）
├── tests/                       # 全离线测试：FakeProvider + httpx ASGITransport
├── verify_real.py               # 真实模型冒烟脚本（凭证仅来自环境变量）
├── requirements.txt
└── pytest.ini
```

## 安装与启动

```powershell
pip install -r requirements.txt

# 凭证只从环境变量读取（密钥一律为占位符，切勿写入任何代码/文件）
$env:RESPONSES_API_KEY = '<key>'        # deepseek-v4-pro（Responses API）
$env:MESSAGES_API_KEY  = '<key>'        # deepseek-v4-flash（Messages API）
# 可选：自定义上游端点。不设置 = 用 SDK 官方端点（那里不存在 deepseek-v4-* 模型，
# 真实冒烟请指向真正提供这两个模型的端点，例如 DeepSeek 官方双协议兼容端点）。
# 拼接规则（重要，实测过 SDK 行为，base 多写/少写一段都会 404）：
#   Responses 适配器: base + /responses    → RESPONSES_BASE_URL 形如 https://api.deepseek.com/v1
#   Messages  适配器: base + /v1/messages  → MESSAGES_BASE_URL  形如 https://api.deepseek.com/anthropic
#     ⚠ 不要写成 .../anthropic/v1：anthropic SDK 会再拼一次 /v1/messages，
#       结果变成 .../anthropic/v1/v1/messages → 404
# 两个协议可共用同一把 DeepSeek API Key。
# $env:RESPONSES_BASE_URL = 'https://api.deepseek.com/v1'
# $env:MESSAGES_BASE_URL  = 'https://api.deepseek.com/anthropic'
# 若课程/平台给的是独立中转地址，以它为准，并按上面的拼接规则保证路径形态正确。

# 启动（uvicorn 工厂模式）
uvicorn gateway.app:create_real_app --factory --host 127.0.0.1 --port 8000
```

离线测试（全程无真实网络请求）：

```powershell
pytest          # 全部离线用例全绿（无真实网络请求），见「功能点证据位置」
```

## API 协议

请求体（自有协议）：

```json
{
  "model": "deepseek-v4-pro",
  "messages": [{"role": "user", "content": "……"}],
  "prompt": {"name": "chat", "version": 2, "variables": {"role": "助手", "domain": "天气", "style": "brief"}},
  "schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
}
```

统一响应（`/chat` 成功 = `LLMResponse`）；HTTP 层错误一律 envelope：

```json
{"error": {"code": "unknown_model", "message": "…", "call_id": "…"}}
```

状态码映射：请求问题 4xx / 限流 429（带 `Retry-After`）/ 上游或网关失败 5xx。
错误码本身即协议：`unknown_model`、`unknown_prompt_template`、
`missing_prompt_variable`、`invalid_prompt_variable`、`schema_validation_failed`、
`rate_limited`、`upstream_error`（未配置备用模型时上游失败）、
`fallback_exhausted`（配置了备用模型但主备全部失败）、
`output_truncated`（结构化输出被长度截断，5xx）、`internal_error`、
`unknown_trace`、`invalid_request`（含入站上下文超预算 400）。

**TTFT 语义**：`ttft_ms` 只属于流式调用（执行开始 → 首个内容块）；
`/chat` 非流式调用没有“首 Token”时刻，`ttft_ms` 如实返回 `None`
（协议字段为 Optional），避免把 E2E 延迟贴成 TTFT 标签。

流式例外：`/stream` 首块发出后的失败不再适用 HTTP envelope，以
`response.failed` 事件结束流（事件：`content.delta` / `content.done` /
`response.failed`）。SSE 线缆格式与课程一致：每个事件两行 ——
`event: <name>` 行 + `data: <json>` 行（事件名不进 data，
浏览器 `EventSource` 的具名监听依赖 `event:` 行）：

### curl 示例

> **Windows shell 引号坑（必读）**：下面第一组是 **bash**（Git Bash / WSL / Linux / macOS）写法。
> 不要直接把它粘进 **cmd.exe** —— cmd 不认单引号，会把 `'{"model":…}'` 连引号一起当参数发给
> 服务器，body 变成非法 JSON，网关会回 `{"error":{"code":"invalid_request",…,"message":"… JSON decode error"}}`。
> 也不要直接在 **PowerShell** 里敲 `curl` —— 那是 `Invoke-RestMethod` 的别名（不是 curl）；
> 请用 `curl.exe` 或下面的 `Invoke-RestMethod` 写法。Windows 的等价写法见第二、三组。

**① bash（Git Bash / WSL / Linux / macOS）：**

```bash
# 1) 普通调用（文本）
curl -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" -H "X-Caller-Id: my-agent" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"用一句话介绍网关"}]}'

# 2) 模板引用（chat v2，多版本 + 条件分支）
curl -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[],
       "prompt":{"name":"chat","version":2,"variables":{"role":"旅行助手","domain":"天气","style":"brief"}}}'

# 3) 结构化输出（出口双层校验）
curl -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"巴黎天气？"}],
       "schema":{"type":"object","properties":{"city":{"type":"string"},"temp_c":{"type":"number"}},"required":["city","temp_c"]}}'

# 4) 流式（text/event-stream；wire 格式为 event: 行 + data: 行）
curl -N http://127.0.0.1:8000/stream -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"数到五"}]}'
# => event: content.delta
#    data: {"delta":"一"}
#    event: content.done
#    data: {"call_id":"…","text":"一二三","ttft_ms":123.4, …}

# 5) 可观测（GET，无 body，各平台通用）
curl -s "http://127.0.0.1:8000/trace?limit=5"
curl -s http://127.0.0.1:8000/trace/<call_id>

# 错误 envelope 示例（未知模型）
curl -si http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"model":"no-such-model","messages":[]}'
# => HTTP/1.1 404 {"error":{"code":"unknown_model", ...}}
```

**② Windows PowerShell（推荐；curl 用 `curl.exe`，JSON 用 PowerShell 单引号字符串，天然不会坏）：**

```powershell
# 把 JSON 存成 PowerShell 字符串（单引号内双引号原样保留）
$chatBody = '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"用一句话介绍网关"}]}'

# 普通调用（文本）
curl.exe -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" -H "X-Caller-Id: my-agent" --data-raw $chatBody

# 模板引用（chat v2）
$tplBody = '{"model":"deepseek-v4-flash","messages":[],"prompt":{"name":"chat","version":2,"variables":{"role":"旅行助手","domain":"天气","style":"brief"}}}'
curl.exe -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" --data-raw $tplBody

# 结构化输出
$schemaBody = '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"巴黎天气？"}],"schema":{"type":"object","properties":{"city":{"type":"string"},"temp_c":{"type":"number"}},"required":["city","temp_c"]}}'
curl.exe -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" --data-raw $schemaBody

# 流式（-N 关闭缓冲，逐帧打印 event:/data:）
$streamBody = '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"数到五"}]}'
curl.exe -sN http://127.0.0.1:8000/stream -H "Content-Type: application/json" --data-raw $streamBody

# 可观测
curl.exe -s "http://127.0.0.1:8000/trace?limit=5"
curl.exe -s "http://127.0.0.1:8000/trace/<call_id>"

# 或者不用 curl，直接用 Invoke-RestMethod（自动转 JSON，中文也安全）
$req = @{ model = 'deepseek-v4-pro'; messages = @(@{ role = 'user'; content = '用一句话介绍网关' }) } |
       ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/chat' -Method Post -ContentType 'application/json' `
                  -Headers @{ 'X-Caller-Id' = 'my-agent' } -Body $req
```

**③ Windows cmd.exe（避免内联转义地狱：把 JSON 写到文件再发）：**

```bat
@echo off
rem 1) 先创建 body.json，UTF-8 无 BOM：
rem    {"model":"deepseek-v4-pro","messages":[{"role":"user","content":"用一句话介绍网关"}]}
rem    含中文时务必用无 BOM 的 UTF-8 保存。

rem 2) 用 --data-binary "@文件" 发送（body 内容原样来自文件，不再有引号问题）
curl -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" -H "X-Caller-Id: my-agent" --data-binary "@body.json"

rem 流式：curl -sN ... --data-binary "@body.json"
rem 可观测：curl -s "http://127.0.0.1:8000/trace?limit=5"

rem 若坚持内联，则必须转义内部双引号（每条 \" 都会被解析成一个 "）：
curl -s http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"model\":\"deepseek-v4-pro\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

## 六大功能点与可验证证据位置

| 功能 | 设计要点 | 真实冒烟（见 verify_real.py 输出） | 离线测试（证据位置） |
| --- | --- | --- | --- |
| **流式** | 逐块转发，事件携带类型与数据；首块后失败 -> `response.failed` | 每模型一次 `/stream`（delta 数/顺序/TTFT） | `tests/test_streaming.py::test_acceptance7_stream_deltas_match_chunk_count_and_order`、`test_acceptance8_stream_failure_after_start_emits_response_failed` |
| **结构化输出** | 双层校验 + 单轮修复；失败返回 `schema_validation_failed`（不变量1） | 每模型一次带 schema 的 `/chat` | `tests/test_chat_and_errors.py::test_acceptance5_valid_json_not_matching_schema`、`tests/test_streaming.py::test_stream_structured_output_failure_not_silently_successful` |
| **模板引用** | name+version+hash 可回放；沙箱渲染；变量缺失/超长直接拒绝 | 每模型一次 chat v2 引用，Trace 打印 hash | `tests/test_templates.py`（条件分支/多版本/无 eval-exec）、`tests/test_chat_and_errors.py::test_acceptance3/4`、`tests/test_trace_and_ratelimit.py::test_trace_records_prompt_name_version_hash` |
| **可观测** | CallTrace：Token 分类 + Cost + Latency + TTFT；`/trace` 列表/详情 | 每调用打印 token/TTFT/latency 证据，末尾打印 trace 摘要 | `tests/test_trace_and_ratelimit.py::test_acceptance9_success_and_failure_traces_with_full_fields` |
| **重试** | 白名单故障指数退避，≤3 次/模型（测试注入 0 退避），预算用尽 -> 能力等价 fallback；备用预算也用尽返回 `fallback_exhausted`（未配置备用时为 `upstream_error`） | 见脚本（真实网络下由上游是否 429/断连触发） | `tests/test_retry_fallback.py::test_acceptance6_retry_then_fallback_succeeds`、`test_acceptance6b_backup_budget_exhausted_clear_error_no_more_switching`、`test_backoff_delays_are_exponential_when_injected`、`tests/test_streaming.py::test_stream_chain_exhausted_before_first_chunk_emits_fallback_exhausted` |
| **限流** | 键 = 逻辑模型，窗口 429 + `Retry-After`，记 Trace、不产生重试；未知模型不占配额；各模型配额独立 | 见脚本（不触发；配额默认足够） | `tests/test_trace_and_ratelimit.py::test_acceptance11_rate_limit_429_retry_after_no_retries`、`test_rate_limit_unknown_model_consumes_no_quota` |

## 三条不变量

1. **未通过 Schema 校验的输出不能作为成功返回** —— 出口校验失败抛
   `schema_validation_failed`（`/chat`）或 `response.failed` 事件（`/stream`），
   绝无静默吞掉或无限修复（修复至多 1 轮）。
2. **流已输出首块后不能切换模型重写** —— 首块后任何失败以 `response.failed`
   结束流（`tests/test_streaming.py`），fallback 只允许在首块之前发生。
3. **无论主/备模型、无论哪个协议适配器，上层只依赖同一份请求/响应/错误协议** ——
   Trace 中的 `model_used` + `adapter` 是其唯一可观测证据
   （`tests/test_retry_fallback.py::test_acceptance6_retry_then_fallback_succeeds`）。

## 截断感知与入站上下文预算守卫（追加特性）

- **截断归一化（适配器内，红线不变）**：Responses 的 `status == "incomplete"` +
  `incomplete_details.reason`（长度类）与 Messages 的 `stop_reason == "max_tokens"`
  各自归一化成统一布尔 `truncated`，随 `ProviderResult` / `ProviderDone` 带出适配器。
- **Trace 落账**：`CallTrace.truncated` 成功、失败（及 aborted）都如实记录。
- **错误分类**：请求带 `json_schema` 且被长度截断 → `output_truncated`（502，
  不再误报 `schema_validation_failed`），错误信息含三处方（缩短上下文 /
  提高 max_tokens / 拆小任务）；无 `json_schema` 的截断**正常返回**，
  `LLMResponse.truncated=True`。
- **入站预算守卫**：字符数粗估 token（中文≈1 字/token，英文≈4 字符/token），
  超出预算（`create_app(max_input_tokens=...)` 可注入，默认 200_000）在模板渲染
  之后、限流/执行之前直接 `invalid_request`（400）拒绝，不占用配额。
- 离线证据：`tests/test_truncation_and_budget.py`（`test_trace_truncated_true_on_error_and_false_on_normal`、
  `test_structured_truncation_returns_output_truncated`、`test_plain_truncation_returns_normally`、
  `test_context_budget_guard_rejects_oversized_input` 等）。

## 真实调用验证

```powershell
# 方式 1（默认）：进程内自建网关跑真实验证。注意：trace 存在该进程内存里，
# 脚本退出即消失 —— 不能用 curl /trace 去查另一个 uvicorn 进程的记录。
python verify_real.py
# 对两个真实模型各完成：非流式 / 流式 / 结构化 / 模板引用，
# 并打印每次调用的 Token 分类统计、TTFT、Latency、Cost（含 model_used，
# 若与请求模型不一致说明走了备用模型 fallback）。
# 说明：TTFT 只存在于流式调用；非流式调用如实打印 ttft_ms = n/a。

# 方式 2：直连外部已运行的网关（凭证/trace 都在那台服务器上，可用 /trace 查询）：
$env:VERIFY_GATEWAY_URL = 'http://127.0.0.1:8000'
python verify_real.py
curl.exe -s "http://127.0.0.1:8000/trace?limit=5"
```

## 最小示例调用方

`examples/mini_agent.py`（`MiniAgent`）只通过网关 HTTP 接口访问模型，代码与
测试中不出现任何供应商 API Key / Base URL（见
`tests/test_example_agent_and_secrets.py`，其中用 `httpx.ASGITransport` 全离线
跑通 chat / stream / 模板三类调用）。
