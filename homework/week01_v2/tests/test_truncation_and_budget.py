"""Length-truncation awareness + inbound context-budget guard.

Covers:
* adapters normalize truncation stop signals into one unified boolean
* CallTrace.truncated lands for success AND failure calls
* structured output + truncation -> ``output_truncated`` (never
  ``schema_validation_failed``); plain text + truncation returns normally
* FakeProvider truncation script step
* inbound context budget guard rejects oversized input with invalid_request(400)
"""

from __future__ import annotations

from gateway.service import estimate_tokens

from fakes import FakeProvider

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "temp_c": {"type": "number"}},
    "required": ["city", "temp_c"],
}

CHAT_BODY = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}


def _trace(client, call_id: str) -> dict:
    return client.get(f"/trace/{call_id}").json()["trace"]


# ---------------------------------------------------------------------------
# (a) trace 落账 truncated（成功与失败都落）
# ---------------------------------------------------------------------------
def test_trace_truncated_true_on_plain_truncated_success(client_factory):
    pro = FakeProvider(text='{"city": "Paris", "temp_c": 21.0}', complete_truncated=True)
    client = client_factory(pro=pro)
    resp = client.post("/chat", json={**CHAT_BODY, "schema": None})
    assert resp.status_code == 200
    assert resp.json()["truncated"] is True
    record = _trace(client, resp.json()["call_id"])
    assert record["status"] == "success"
    assert record["truncated"] is True


def test_trace_truncated_true_on_error_and_false_on_normal(client_factory):
    pro = FakeProvider(text='{"city": "Paris", "temp_c": 21.0}', complete_truncated=True)
    client = client_factory(pro=pro)
    bad = client.post("/chat", json={**CHAT_BODY, "schema": WEATHER_SCHEMA})
    assert bad.status_code == 502
    record = _trace(client, bad.json()["error"]["call_id"])
    assert record["status"] == "error"
    assert record["truncated"] is True
    assert record["error_code"] == "output_truncated"

    ok = client.post(
        "/chat",
        json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
    )  # 正常（非截断）的 flash 提供者
    assert ok.status_code == 200
    record = _trace(client, ok.json()["call_id"])
    assert record["truncated"] is False


# ---------------------------------------------------------------------------
# (b) 带 Schema 的截断 → output_truncated（而非 schema_validation_failed）
# ---------------------------------------------------------------------------
def test_structured_truncation_returns_output_truncated(client_factory):
    pro = FakeProvider(text='{"city": "Par', complete_truncated=True)
    client = client_factory(pro=pro)
    resp = client.post("/chat", json={**CHAT_BODY, "schema": WEATHER_SCHEMA})
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "output_truncated"
    assert error["code"] != "schema_validation_failed"
    # 错误信息写明三个处方
    for hint in ("缩短上下文", "提高 max_tokens", "拆小任务"):
        assert hint in error["message"]


def test_structured_failure_without_truncation_still_schema_validation_failed(client_factory):
    # 回归：非截断的 schema 失败仍是 schema_validation_failed
    pro = FakeProvider(text='{"city": 123, "temp_c": "nope"}', complete_truncated=False)
    client = client_factory(pro=pro)
    resp = client.post("/chat", json={**CHAT_BODY, "schema": WEATHER_SCHEMA})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "schema_validation_failed"


# ---------------------------------------------------------------------------
# (c) 无 Schema 的截断 → 正常返回，不报错
# ---------------------------------------------------------------------------
def test_plain_truncation_returns_normally(client_factory):
    pro = FakeProvider(text="only a partial answer", complete_truncated=True)
    client = client_factory(pro=pro)
    resp = client.post("/chat", json=CHAT_BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "only a partial answer"
    assert body["truncated"] is True
    assert body["structured"] is None


def test_stream_plain_truncation_done_carries_flag(client_factory):
    pro = FakeProvider(chunks=["partial ", "reply"], stream_truncated=True)
    client = client_factory(pro=pro)
    with client.stream("POST", "/stream", json=CHAT_BODY) as resp:
        from conftest import sse_events

        events = sse_events(resp)
    done = [e for e in events if e["event"] == "content.done"][0]["data"]
    assert done["truncated"] is True
    assert done["text"] == "partial reply"


def test_stream_structured_truncation_emits_output_truncated(client_factory):
    pro = FakeProvider(chunks=["{", '"city": "Par'], stream_truncated=True)
    client = client_factory(pro=pro)
    with client.stream("POST", "/stream", json={**CHAT_BODY, "schema": WEATHER_SCHEMA}) as resp:
        from conftest import sse_events

        events = sse_events(resp)
    failed = [e for e in events if e["event"] == "response.failed"][-1]
    assert failed["data"]["code"] == "output_truncated"
    assert "content.done" not in [e["event"] for e in events]


# ---------------------------------------------------------------------------
# 字符估算 + 入站上下文预算守卫
# ---------------------------------------------------------------------------
def test_estimate_tokens_roughly_per_language():
    assert estimate_tokens("") == 0
    assert estimate_tokens("中文") == 2          # 中文≈1 字 1 token
    assert estimate_tokens("abcd") == 1          # 英文≈4 字符 1 token
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("混排hello") >= 3     # 混合文本粗略成立即可


def test_context_budget_guard_rejects_oversized_input(client_factory):
    pro = FakeProvider(text="should not run")
    client = client_factory(pro=pro, max_input_tokens=40)
    big = "x" * 400  # 估算 ≈ 100 tokens > 40
    resp = client.post(
        "/chat", json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": big}]}
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "invalid_request"
    assert "100" in error["message"] or "40" in error["message"]
    assert pro.complete_calls == 0  # 未进入执行层


def test_context_budget_guard_allows_within_budget(client_factory):
    client = client_factory(max_input_tokens=40)
    resp = client.post("/chat", json=CHAT_BODY)
    assert resp.status_code == 200


def test_context_budget_guard_after_unknown_model(client_factory):
    # 白名单校验先于预算校验：模型不存在时仍报 unknown_model
    client = client_factory(max_input_tokens=40)
    resp = client.post(
        "/chat",
        json={"model": "ghost", "messages": [{"role": "user", "content": "x" * 400}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_model"


def test_context_budget_guard_does_not_consume_rate_quota(client_factory):
    pro = FakeProvider(text="ok")
    client = client_factory(pro=pro, max_input_tokens=40, rate_limits={"deepseek-v4-pro": 1})
    # 占满唯一配额
    assert client.post("/chat", json=CHAT_BODY).status_code == 200
    # 预算拒绝发生在限流之前：超预算请求不占配额也不触发 429 混淆
    resp = client.post(
        "/chat",
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "x" * 400}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
