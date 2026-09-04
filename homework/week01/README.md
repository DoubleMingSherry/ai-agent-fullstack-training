# Mini LLM Gateway（week01 作业）

统一入口访问不同模型的迷你网关，需求见 `my_gateway.md`。
验收 = `pytest` 全绿，全程离线（无任何真实网络请求）。

```bash
pip install -r requirements.txt
pytest
```

## 五层结构

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 1 接口层 | `gateway/app.py` `gateway/protocol.py` | FastAPI 三入口（`/chat` `/stream` `/trace`）、Pydantic 协议、统一错误 envelope |
| 2 治理层 | `gateway/models.py` `gateway/prompts.py` `gateway/ratelimit.py` | 模型白名单与能力等价校验、模板注册表（name+version+hash）与沙箱渲染、按 `X-Caller-Id` 限流 |
| 3 执行层 | `gateway/provider.py` `gateway/executor.py` | OpenAI Compatible Provider（`max_retries=0`）、恰好 1 次重试 + 至多 1 次 fallback、流式转发与首 Token 红线 |
| 4 出口校验层 | `gateway/structured.py` `gateway/streamjson.py` | 双层校验（请求带 Schema + 返回后 Pydantic 严格校验）、提取/修复合计 ≤1 轮、流式 JSON 增量解析 |
| 5 可观测层 | `gateway/trace.py` | CallTrace：model_used、attempts、status、错误码、prompt name+version+hash、Token/Cost/Latency |

## 关键边界

- **错误码即协议**：`{error: {code, message, call_id}}`；请求问题 4xx、限流 429、上游/网关失败 5xx。
- **限流 429 ≠ 上游 429**：前者是接口层拒绝（不计 attempts、不重试），后者在执行层 `is_retryable` 白名单内。
- **不变量 1**：未通过 Schema 校验的输出只能以 `schema_validation_failed` 失败收场。
- **不变量 2**：流发出首块后失败只产生 `response.failed`，不换模型重写。
- **不变量 3**：主/备模型对上层暴露同一份 `LLMResponse` 与错误 envelope；`model_used` 是唯一可观测证据。
- **红线**：SDK completion 对象不离开 Provider；重试只有 Gateway 一层说了算。

## 示例调用方

`caller/business_agent.py`：只通过网关 HTTP 接口访问模型（仅网关地址 + 网关协议），
源码不含任何供应商 API Key / Base URL。启动网关：

```bash
uvicorn gateway.app:create_app --factory   # 生产模式从 GATEWAY_PROVIDER_API_KEY 等环境变量装配
```
