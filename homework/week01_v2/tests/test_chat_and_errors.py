"""Acceptance 1–5: LLMResponse / unknown_model / unknown_prompt_template /
missing_prompt_variable / schema_validation_failed."""

from __future__ import annotations

import pytest

from fakes import FakeProvider

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "temp_c": {"type": "number"},
        "conditions": {"type": "string"},
    },
    "required": ["city", "temp_c", "conditions"],
}


def _chat(client, body: dict, caller: str = "tester") -> dict:
    return client.post("/chat", json=body, headers={"X-Caller-Id": caller})


def test_acceptance1_plain_text_returns_llm_response(client):
    response = _chat(
        client,
        {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "answer from pro"
    assert body["model"] == "deepseek-v4-pro"
    assert body["model_used"] == "deepseek-v4-pro"
    assert body["adapter"] == "fake"
    assert body["attempts"] == 1
    assert body["usage"]["input_tokens"] >= 0 and body["usage"]["output_tokens"] >= 0
    assert body["cost"] >= 0.0
    assert body["latency_ms"] >= 0.0
    # TTFT 只属于流式；非流式调用没有“首 Token”，必须如实为 None
    # （而非把 E2E 延迟贴上 TTFT 标签）
    assert body["ttft_ms"] is None
    assert body["prompt"] is None
    assert isinstance(body["call_id"], str) and body["call_id"]


def test_acceptance2_unknown_model_returns_error_code(client):
    response = _chat(client, {"model": "no-such-model", "messages": []})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_model"


def test_acceptance3_unknown_prompt_template_version(client):
    # name exists but version does not
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [],
            "prompt": {"name": "chat", "version": 99, "variables": {}},
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_prompt_template"


def test_acceptance3b_unknown_prompt_template_name(client):
    response = _chat(
        client,
        {"model": "deepseek-v4-pro", "messages": [], "prompt": {"name": "ghost"}},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_prompt_template"


def test_acceptance4_missing_prompt_variable(client):
    # chat v2 requires role + domain; domain is missing
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [],
            "prompt": {"name": "chat", "version": 2, "variables": {"role": "assistant"}},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_prompt_variable"


def test_missing_variable_never_reaches_upstream(client_factory):
    pro = FakeProvider(text="should not be called")
    client = client_factory(pro=pro)
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [],
            "prompt": {"name": "chat", "version": 2, "variables": {"role": "x"}},
        },
    )
    assert response.status_code == 400
    assert pro.complete_calls == 0  # request was rejected before the execution layer


def test_overlong_variable_rejected(client):
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [],
            "prompt": {
                "name": "chat",
                "version": 2,
                "variables": {"role": "x", "domain": "y" * 5000},
            },
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_prompt_variable"


def test_structured_output_success_and_first_layer_schema(client_factory):
    payload = {"city": "Paris", "temp_c": 21.0, "conditions": "sunny"}
    pro = FakeProvider(text='{"city": "Paris", "temp_c": 21.0, "conditions": "sunny"}')
    client = client_factory(pro=pro)
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "weather?"}],
            "schema": WEATHER_SCHEMA,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["structured"] == payload
    # layer 1: the business schema must have been handed to the adapter
    assert pro.last_request is not None and pro.last_request.json_schema == WEATHER_SCHEMA


def test_acceptance5_valid_json_not_matching_schema(client_factory):
    pro = FakeProvider(text='{"ok": true}')
    client = client_factory(pro=pro)
    response = _chat(
        client,
        {
            "model": "deepseek-v4-pro",
            "messages": [],
            "schema": WEATHER_SCHEMA,
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "schema_validation_failed"
    # invariant 1: no success payload leaked alongside the error
    assert "text" not in body and "model_used" not in body


def test_repair_round_recovers_fenced_json(client_factory):
    pro = FakeProvider(text='Here you go:\n```json\n{"city": "Rome", "temp_c": 18, "conditions": "rain"}\n```')
    client = client_factory(pro=pro)
    response = _chat(
        client,
        {"model": "deepseek-v4-pro", "messages": [], "schema": WEATHER_SCHEMA},
    )
    assert response.status_code == 200
    assert response.json()["structured"]["city"] == "Rome"


def test_unrepairable_output_returns_schema_validation_failed(client_factory):
    pro = FakeProvider(text="no json here at all")
    client = client_factory(pro=pro)
    response = _chat(
        client,
        {"model": "deepseek-v4-pro", "messages": [], "schema": WEATHER_SCHEMA},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_validation_failed"
