"""可观测层：验收 9（成功与失败都有 Trace，字段齐全）与 /trace 查询协议。"""

from conftest import FakeProvider, chat_payload, get_json, make_app, post_json


def test_success_and_failure_traced_with_full_fields():
    provider = FakeProvider(
        [
            {"kind": "text", "text": "成功响应"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
        ]
    )
    app = make_app(provider)

    ok = post_json(app, "/chat", chat_payload()).json()
    bad = post_json(app, "/chat", chat_payload(model="chat-pro")).json()

    traces = get_json(app, "/trace").json()["traces"]
    assert [t["call_id"] for t in traces] == [ok["call_id"], bad["error"]["call_id"]]

    success = traces[0]
    for field in (
        "call_id",
        "model_used",
        "attempts",
        "status",
        "error_code",
        "prompt",
        "input_tokens",
        "output_tokens",
        "cost",
        "latency_ms",
    ):
        assert field in success, f"Trace 缺少字段: {field}"
    assert success["status"] == "ok"
    assert success["model_used"] == "chat-lite"
    assert success["attempts"] == 1
    assert success["error_code"] is None
    assert success["prompt"] == {
        "name": "answer",
        "version": "2",
        "hash": success["prompt"]["hash"],
    }
    assert success["input_tokens"] == 10 and success["output_tokens"] == 5
    assert success["cost"] > 0
    assert success["latency_ms"] >= 0

    failure = traces[1]
    assert failure["status"] == "failed"
    assert failure["error_code"] == "upstream_error"
    assert failure["model_used"] == "chat-pro"
    assert failure["attempts"] == 2


def test_trace_detail_and_unknown_call():
    provider = FakeProvider([{"kind": "text", "text": "ok"}])
    app = make_app(provider)
    ok = post_json(app, "/chat", chat_payload()).json()

    detail = get_json(app, f"/trace/{ok['call_id']}").json()
    assert detail["call_id"] == ok["call_id"]

    missing = get_json(app, "/trace/call-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "unknown_call"


def test_trace_model_used_is_actual_model():
    # 不变量 3 的唯一可观测证据：fallback 后 model_used 是备用模型
    provider = FakeProvider(
        [
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},  # 重试用尽 → 进入备用模型
            {"kind": "text", "text": "来自备用模型"},
        ]
    )
    app = make_app(provider)
    ok = post_json(app, "/chat", chat_payload(model="chat-lite")).json()

    trace = get_json(app, f"/trace/{ok['call_id']}").json()
    assert trace["requested_model"] == "chat-lite"
    assert trace["model_used"] == "chat-lite-backup"
