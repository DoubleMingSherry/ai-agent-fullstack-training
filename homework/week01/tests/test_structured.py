"""出口校验层：验收 5（合法 JSON 但不符合 Schema → schema_validation_failed）
与修复边界（不静默吞掉、不无限重试）。"""

from conftest import FakeProvider, chat_payload, make_app, post_json

SCORE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["answer", "score"],
}


def _app(text: str):
    provider = FakeProvider([{"kind": "text", "text": text}])
    return make_app(provider)


def test_valid_json_but_schema_mismatch():
    # 合法 JSON：缺 required 字段
    response = post_json(
        _app('{"answer": "42"}'),
        "/chat",
        chat_payload(response_schema=SCORE_SCHEMA),
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "schema_validation_failed"
    assert "score" in error["message"]


def test_valid_json_but_wrong_type():
    response = post_json(
        _app('{"answer": "42", "score": "5"}'),
        "/chat",
        chat_payload(response_schema=SCORE_SCHEMA),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "schema_validation_failed"


def test_valid_json_with_extra_field_rejected():
    response = post_json(
        _app('{"answer": "42", "score": 5, "extra": true}'),
        "/chat",
        chat_payload(response_schema=SCORE_SCHEMA),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "schema_validation_failed"


def test_non_json_output_rejected_without_retry():
    provider = FakeProvider([{"kind": "text", "text": "抱歉，我不知道。"}])
    app = make_app(provider)

    response = post_json(
        app, "/chat", chat_payload(response_schema=SCORE_SCHEMA)
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "schema_validation_failed"
    assert len(provider.calls) == 1  # 修复边界：至多 1 轮，不无限重试


def test_invalid_business_schema_is_a_request_problem():
    # Schema 本身非法属于请求问题（4xx），在进入执行层前拒绝
    response = post_json(
        _app('{"answer": "42", "score": 5}'),
        "/chat",
        chat_payload(response_schema={"type": "string"}),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
