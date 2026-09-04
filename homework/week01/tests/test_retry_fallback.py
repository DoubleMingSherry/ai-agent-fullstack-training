"""执行层：验收 6（恰好 1 次重试 → fallback；备用也失败返回明确错误码）
与 is_retryable 白名单边界。"""

from conftest import FakeProvider, chat_payload, get_json, make_app, post_json

SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"}},
    "required": ["category"],
}


def test_retry_once_then_fallback_success():
    # 主模型连接失败：恰好 1 次重试 → 能力等价备用模型成功
    provider = FakeProvider(
        [
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "text", "text": "备用模型响应"},
        ]
    )
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="chat-lite"))

    assert response.status_code == 200
    body = response.json()
    assert body["model_used"] == "chat-lite-backup"  # 不变量 3：同一份协议
    assert body["attempts"] == 3  # 主模型 2 次（含 1 次重试）+ 备用 1 次
    assert body["text"] == "备用模型响应"
    assert len(provider.calls) == 3


def test_fallback_also_fails_returns_explicit_code():
    # 备用模型重试也失败 → fallback_exhausted，不再换模型（共 4 次尝试）
    provider = FakeProvider(
        [
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
        ]
    )
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="chat-lite"))

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "fallback_exhausted"
    assert len(provider.calls) == 4  # 主备各 2 次，链上没有第三个模型


def test_primary_without_fallback_gives_upstream_error():
    # chat-pro 未配置备用：1 次重试后明确失败，不跨能力档位换模型
    provider = FakeProvider(
        [
            {"kind": "error", "error": "timeout"},
            {"kind": "error", "error": "timeout"},
        ]
    )
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="chat-pro"))

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert len(provider.calls) == 2


def test_non_retryable_failure_skips_retry_goes_to_fallback():
    # server 类上游错误不在 is_retryable 白名单：不重试，直接 fallback
    provider = FakeProvider(
        [
            {"kind": "error", "error": "server"},
            {"kind": "text", "text": "备用响应"},
        ]
    )
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="chat-lite"))

    assert response.status_code == 200
    body = response.json()
    assert body["model_used"] == "chat-lite-backup"
    assert body["attempts"] == 2


def test_upstream_429_is_retryable():
    # 上游 429 属于执行层白名单（区别于接口层限流 429）
    provider = FakeProvider(
        [
            {"kind": "error", "error": "rate_limit"},
            {"kind": "text", "text": "恢复后的响应"},
        ]
    )
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="chat-lite"))

    assert response.status_code == 200
    assert response.json()["attempts"] == 2


def test_exhausted_failure_recorded_in_trace():
    provider = FakeProvider(
        [
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
            {"kind": "error", "error": "connect"},
        ]
    )
    app = make_app(provider)
    response = post_json(app, "/chat", chat_payload(model="chat-lite"))
    call_id = response.json()["error"]["call_id"]

    trace = get_json(app, f"/trace/{call_id}").json()
    assert trace["status"] == "failed"
    assert trace["error_code"] == "fallback_exhausted"
    assert trace["attempts"] == 4
    assert trace["model_used"] == "chat-lite-backup"
