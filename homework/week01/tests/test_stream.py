"""执行层流式：验收 7（k 块逐块转发）、验收 8（首块后失败 → response.failed）
与 Structured Streaming 增量解析边界。"""

from conftest import (
    FakeProvider,
    chat_payload,
    deltas_of,
    first_error,
    make_app,
    post_json,
    stream_once,
)

SCORE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["answer", "score"],
}


def test_k_chunks_forwarded_in_order():
    # FakeProvider 分 3 块 → /stream 逐块产生 content.delta，数量与顺序一致
    provider = FakeProvider([{"kind": "stream", "chunks": ["你好", "，", "世界"]}])
    app = make_app(provider)

    status, events = stream_once(app, chat_payload())

    assert status == 200
    assert deltas_of(events) == ["你好", "，", "世界"]
    terminal = events[-1]
    assert terminal[0] == "response.completed"
    assert terminal[1]["model_used"] == "chat-lite"
    assert terminal[1]["attempts"] == 1
    assert terminal[1]["usage"]["input_tokens"] == 7


def test_retry_before_first_chunk():
    # 首 Token 发出前失败：遵守与普通调用相同的重试/fallback 契约
    provider = FakeProvider(
        [
            {"kind": "stream", "chunks": [], "fail_after": 0, "error": "connect"},
            {"kind": "stream", "chunks": ["重试后第一块", "第二块"]},
        ]
    )
    app = make_app(provider)

    status, events = stream_once(app, chat_payload())

    assert status == 200
    assert deltas_of(events) == ["重试后第一块", "第二块"]
    assert events[-1][1]["attempts"] == 2
    assert len(provider.calls) == 2


def test_failure_after_first_chunk_ends_stream_no_model_switch():
    # 不变量 2：首块已发出后失败 → response.failed，不能换模型重写
    provider = FakeProvider(
        [{"kind": "stream", "chunks": ["he", "llo"], "fail_after": 1, "error": "connect"}]
    )
    app = make_app(provider)

    status, events = stream_once(app, chat_payload())

    assert status == 200  # HTTP 层已正常开流，失败体现在事件里
    assert deltas_of(events) == ["he"]  # 只有首块发出
    error = first_error(events)
    assert error is not None and error["code"] == "upstream_error"
    assert len(provider.calls) == 1  # 没有重试、没有换模型


def test_all_attempts_fail_before_first_chunk():
    provider = FakeProvider(
        [
            {"kind": "stream", "chunks": [], "fail_after": 0, "error": "connect"},
            {"kind": "stream", "chunks": [], "fail_after": 0, "error": "connect"},
            {"kind": "stream", "chunks": [], "fail_after": 0, "error": "connect"},
            {"kind": "stream", "chunks": [], "fail_after": 0, "error": "connect"},
        ]
    )
    app = make_app(provider)

    status, events = stream_once(app, chat_payload(model="chat-lite"))

    assert status == 200
    error = first_error(events)
    assert error is not None and error["code"] == "fallback_exhausted"
    assert len(provider.calls) == 4


def test_stream_structured_incremental_json_events():
    # 增量解析：不等全部 chunk 到齐，顶层键完成即发 json.partial
    provider = FakeProvider(
        [{"kind": "stream", "chunks": ['{"answer"', ':"幂等"', ',"score":5}']}]
    )
    app = make_app(provider)

    status, events = stream_once(app, chat_payload(response_schema=SCORE_SCHEMA))

    assert status == 200
    partials = [data for name, data in events if name == "json.partial"]
    assert [p["key"] for p in partials] == ["answer", "score"]  # 顺序交付
    assert partials[0]["value"] == "幂等"
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["data"] == {"answer": "幂等", "score": 5}


def test_stream_structured_final_validation_fails():
    # 流结束后做最终校验：不合法 → response.failed(schema_validation_failed)
    provider = FakeProvider(
        [{"kind": "stream", "chunks": ['{"answer"', ':"幂等"}']}]
    )
    app = make_app(provider)

    status, events = stream_once(app, chat_payload(response_schema=SCORE_SCHEMA))

    assert status == 200
    error = first_error(events)
    assert error is not None and error["code"] == "schema_validation_failed"
    assert not any(name == "response.completed" for name, _ in events)


def test_stream_governance_errors_still_use_http_envelope():
    # 首块未发出前的错误（未知模型）仍走统一 HTTP envelope，而非流事件
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(app, "/stream", chat_payload(model="nope-model"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_model"
