"""Acceptance 9 & 11: /trace observability fields and per-model rate limiting
(429 + Retry-After, recorded in trace, no retries, quotas independent)."""

from __future__ import annotations

from fakes import FakeProvider

CHAT_V2_VARS = {"role": "travel guide", "domain": "weather", "style": "brief"}


def test_acceptance9_success_and_failure_traces_with_full_fields(client_factory):
    pro = FakeProvider(text='{"ok": true}')
    client = client_factory(pro=pro)

    ok = client.post(
        "/chat",
        json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Caller-Id": "alice"},
    )
    assert ok.status_code == 200
    ok_id = ok.json()["call_id"]

    bad = client.post(
        "/chat",
        json={
            "model": "deepseek-v4-pro",
            "messages": [],
            "schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
        headers={"X-Caller-Id": "bob"},
    )
    assert bad.status_code == 422
    bad_id = bad.json()["error"]["call_id"]

    traces = client.get("/trace").json()["traces"]
    assert {t["status"] for t in traces} >= {"success", "error"}

    ok_record = next(t for t in traces if t["call_id"] == ok_id)
    for key in (
        "call_id", "caller_id", "model", "model_used", "adapter", "attempts",
        "status", "error_code", "usage", "cost", "latency_ms", "ttft_ms", "prompt",
    ):
        assert key in ok_record, f"trace missing {key}"
    assert ok_record["caller_id"] == "alice"
    assert ok_record["model_used"] == "deepseek-v4-flash"
    assert ok_record["adapter"] == "fake"
    assert ok_record["attempts"] == 1
    assert ok_record["status"] == "success"
    assert ok_record["usage"]["output_tokens"] == 5
    assert ok_record["cost"] > 0.0
    assert ok_record["latency_ms"] >= 0.0
    # 非流式调用无首 Token：ttft_ms 字段存在但为 None
    assert ok_record["ttft_ms"] is None

    bad_record = next(t for t in traces if t["call_id"] == bad_id)
    assert bad_record["status"] == "error"
    assert bad_record["error_code"] == "schema_validation_failed"
    assert bad_record["model_used"] == "deepseek-v4-pro"

    # detail endpoint agrees
    detail = client.get(f"/trace/{ok_id}").json()["trace"]
    assert detail["call_id"] == ok_id


def test_trace_records_prompt_name_version_hash(client_factory):
    client = client_factory()
    resp = client.post(
        "/chat",
        json={
            "model": "deepseek-v4-pro",
            "messages": [],
            "prompt": {"name": "chat", "version": 2, "variables": CHAT_V2_VARS},
        },
    )
    assert resp.status_code == 200
    call_id = resp.json()["call_id"]

    from gateway.templates import builtin_templates

    expected = builtin_templates().resolve("chat", 2)
    record = client.get(f"/trace/{call_id}").json()["trace"]
    prompt = record["prompt"]
    assert prompt["name"] == "chat"
    assert prompt["version"] == 2
    assert prompt["hash"] == expected.hash  # replayability of behaviour


def test_trace_list_filter_by_caller(client_factory):
    client = client_factory()
    client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []}, headers={"X-Caller-Id": "carol"})
    client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []}, headers={"X-Caller-Id": "dave"})
    filtered = client.get("/trace", params={"caller_id": "carol"}).json()["traces"]
    assert len(filtered) == 1 and filtered[0]["caller_id"] == "carol"


def test_unknown_trace_returns_envelope(client):
    response = client.get("/trace/no-such-call")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_trace"


def test_acceptance11_rate_limit_429_retry_after_no_retries(client_factory):
    pro = FakeProvider(text="pro")
    flash = FakeProvider(text="flash")
    # tiny injected quota for one model only
    client = client_factory(pro=pro, flash=flash, rate_limits={"deepseek-v4-pro": 2})

    first = client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []})
    second = client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []})
    assert first.status_code == 200 and second.status_code == 200

    third = client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []})
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert int(third.headers["Retry-After"]) >= 1
    limited_id = third.json()["error"]["call_id"]

    # interface-layer refusal: no execution layer involvement at all
    assert pro.complete_calls == 2
    trace = client.get(f"/trace/{limited_id}").json()["trace"]
    assert trace["status"] == "error"
    assert trace["error_code"] == "rate_limited"
    assert trace["attempts"] == 0
    assert trace["model_used"] is None

    # different model's quota is independent -> still allowed
    other = client.post("/chat", json={"model": "deepseek-v4-flash", "messages": []})
    assert other.status_code == 200
    assert flash.complete_calls == 1
    other_trace = client.get(f"/trace/{other.json()['call_id']}").json()["trace"]
    assert other_trace["status"] == "success"
    assert other_trace["attempts"] == 1


def test_rate_limit_unknown_model_consumes_no_quota(client_factory):
    pro = FakeProvider(text="pro")
    client = client_factory(pro=pro, rate_limits={"deepseek-v4-pro": 1})
    # consume the only allowed request
    assert client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []}).status_code == 200
    # an unknown model is refused BEFORE the limiter and burns nothing
    unknown = client.post("/chat", json={"model": "nope", "messages": []})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "unknown_model"
    # the single quota for pro was consumed, but unknown models never counted
    blocked = client.post("/chat", json={"model": "deepseek-v4-pro", "messages": []})
    assert blocked.status_code == 429
