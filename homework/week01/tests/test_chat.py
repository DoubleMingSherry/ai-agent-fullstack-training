"""验收 1：正常文本/结构化请求返回统一 LLMResponse。"""

import pytest

from conftest import FakeProvider, chat_payload, make_app, post_json


def test_chat_text_returns_llm_response():
    provider = FakeProvider([{"kind": "text", "text": "幂等指同一操作执行多次结果一致。"}])
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["call_id"].startswith("call-")
    assert body["model_used"] == "chat-lite"
    assert body["text"] == "幂等指同一操作执行多次结果一致。"
    assert body["data"] is None
    assert body["prompt"] == {
        "name": "answer",
        "version": "2",
        "hash": body["prompt"]["hash"],  # 有 hash 且为 64 位十六进制
    }
    assert len(body["prompt"]["hash"]) == 64
    assert body["attempts"] == 1
    assert body["usage"]["input_tokens"] == 10
    assert body["usage"]["output_tokens"] == 5
    assert body["latency_ms"] >= 0


def test_chat_cost_from_pricing_table():
    # chat-lite 定价：0.001/1K 输入，0.002/1K 输出 → (10,5) → 0.00002
    provider = FakeProvider([{"kind": "text", "text": "ok"}])
    app = make_app(provider)
    body = post_json(app, "/chat", chat_payload()).json()
    assert body["usage"]["cost"] == pytest.approx(2e-05)


def test_chat_structured_returns_validated_data():
    provider = FakeProvider([{"kind": "text", "text": '{"answer": "42", "score": 5}'}])
    app = make_app(provider)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
        "required": ["answer", "score"],
    }

    body = post_json(app, "/chat", chat_payload(response_schema=schema)).json()

    assert body["data"] == {"answer": "42", "score": 5}
    assert body["text"] == '{"answer": "42", "score": 5}'


def test_chat_structured_extracts_fenced_json_one_repair_round():
    # 直接解析失败 → 提取（修复 1 轮）成功；只消耗 1 次上游调用
    provider = FakeProvider(
        [{"kind": "text", "text": '结果如下：```json\n{"answer": "42", "score": 5}\n```'}]
    )
    app = make_app(provider)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
        "required": ["answer", "score"],
    }

    body = post_json(app, "/chat", chat_payload(response_schema=schema)).json()

    assert body["data"] == {"answer": "42", "score": 5}
    assert len(provider.calls) == 1
