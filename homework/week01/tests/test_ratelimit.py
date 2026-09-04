"""治理层限流：验收 11（超配额 429 + Retry-After，记入 Trace，不产生重试）
与边界区分（接口层限流 429 ≠ 上游 429）。"""

from conftest import FakeProvider, chat_payload, get_json, make_app, post_json


def test_quota_exceeded_returns_429_with_retry_after():
    provider = FakeProvider([{"kind": "text", "text": "第 1 次"}])
    app = make_app(provider, quota=1)  # 测试注入小配额

    first = post_json(
        app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-a"}
    )
    assert first.status_code == 200

    second = post_json(
        app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-a"}
    )

    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert int(second.headers["Retry-After"]) >= 1
    assert len(provider.calls) == 1  # 请求不进入执行层


def test_rate_limited_request_recorded_in_trace_without_attempts():
    provider = FakeProvider([{"kind": "text", "text": "第 1 次"}])
    app = make_app(provider, quota=1)
    post_json(app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-a"})
    response = post_json(
        app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-a"}
    )
    call_id = response.json()["error"]["call_id"]

    trace = get_json(app, f"/trace/{call_id}").json()
    assert trace["status"] == "failed"
    assert trace["error_code"] == "rate_limited"
    assert trace["attempts"] == 0  # 接口层拒绝，不计 attempts
    assert trace["model_used"] is None


def test_callers_have_independent_quotas():
    provider = FakeProvider(
        [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}]
    )
    app = make_app(provider, quota=1)

    a = post_json(app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-a"})
    b = post_json(app, "/chat", chat_payload(), headers={"X-Caller-Id": "caller-b"})

    assert a.status_code == 200
    assert b.status_code == 200


def test_default_caller_id_when_header_missing():
    provider = FakeProvider(
        [{"kind": "text", "text": "1"}, {"kind": "text", "text": "2"}]
    )
    app = make_app(provider, quota=1)

    first = post_json(app, "/chat", chat_payload())  # 无 X-Caller-Id → default
    second = post_json(app, "/chat", chat_payload())

    assert first.status_code == 200
    assert second.status_code == 429  # 同一 default 配额耗尽
    assert len(provider.calls) == 1
