"""Acceptance 7–8 (+ streaming structured-output red lines):
/stream emits one content.delta per provider chunk in order; a failure after
the stream started produces response.failed and never silently swaps models.
"""

from __future__ import annotations

import json

from fakes import FakeProvider, retryable_error
from conftest import sse_events

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "temp_c": {"type": "number"}},
    "required": ["city", "temp_c"],
}


def _stream(client, body: dict, caller: str = "streamer"):
    return client.stream(
        "POST", "/stream", json=body, headers={"X-Caller-Id": caller}
    )


def test_acceptance7_stream_deltas_match_chunk_count_and_order(client_factory):
    chunks = ["The ", "quick ", "brown fox"]
    pro = FakeProvider(text="".join(chunks), chunks=chunks)
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "q"}]}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)
    deltas = [e["data"]["delta"] for e in events if e["event"] == "content.delta"]
    assert deltas == chunks  # count == k, order preserved
    done = [e for e in events if e["event"] == "content.done"]
    assert len(done) == 1
    assert done[0]["data"]["text"] == "".join(chunks)
    assert done[0]["data"]["model_used"] == "deepseek-v4-pro"
    assert done[0]["data"]["ttft_ms"] is not None
    assert not [e for e in events if e["event"] == "response.failed"]


def test_stream_content_done_carries_stats_and_attempts(client_factory):
    pro = FakeProvider(chunks=["hello ", "world"], input_tokens=7, output_tokens=2)
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": []}) as response:
        events = sse_events(response)
    done = [e for e in events if e["event"] == "content.done"][0]["data"]
    assert done["usage"]["input_tokens"] == 7
    assert done["usage"]["output_tokens"] == 2
    assert done["cost"] > 0.0
    assert done["latency_ms"] >= 0.0
    assert done["attempts"] == 1


def test_acceptance8_stream_failure_after_start_emits_response_failed(client_factory):
    # provider streams "first" chunk then raises a *retryable* error mid-stream
    pro = FakeProvider(stream_plan=["first chunk", retryable_error("mid-stream boom")])
    flash = FakeProvider(chunks=["should never be used"])
    client = client_factory(pro=pro, flash=flash)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": []}) as response:
        events = sse_events(response)
    assert response.status_code == 200  # SSE body carries the failure event
    kinds = [e["event"] for e in events]
    assert kinds == ["content.delta", "response.failed"]
    assert events[0]["data"]["delta"] == "first chunk"
    assert events[-1]["data"]["code"] == "upstream_error"
    # no model swap / rewrite after the first chunk (invariant 2)
    assert flash.stream_opens == 0


def test_stream_retry_allowed_before_first_chunk(client_factory):
    # fails on the first two stream opens (before any content) then succeeds
    pro = FakeProvider(
        chunks=["finally ok"],
        stream_failures=[retryable_error("open-1"), retryable_error("open-2")],
    )
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": []}) as response:
        events = sse_events(response)
    assert [e["event"] for e in events] == ["content.delta", "content.done"]
    assert events[0]["data"]["delta"] == "finally ok"
    assert pro.stream_opens == 3  # 2 failed opens + 1 success (attempts=3)
    assert events[-1]["data"]["attempts"] == 3


def test_stream_structured_output_success(client_factory):
    plan = ['{"city": "Paris"', ', "temp_c": 22.0}']
    pro = FakeProvider(stream_plan=plan)
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": [], "schema": WEATHER_SCHEMA}) as response:
        events = sse_events(response)
    kinds = [e["event"] for e in events]
    assert kinds[-1] == "content.done"
    done = events[-1]["data"]
    assert done["structured"] == {"city": "Paris", "temp_c": 22.0}
    assert done["text"] == "".join(plan)


def test_stream_structured_output_failure_not_silently_successful(client_factory):
    pro = FakeProvider(stream_plan=['{"city": 123, "temp_c": "nope"}'])
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": [], "schema": WEATHER_SCHEMA}) as response:
        events = sse_events(response)
    kinds = [e["event"] for e in events]
    assert "content.done" not in kinds  # invariant 1: invalid output is not a success
    assert kinds[-1] == "response.failed"
    assert events[-1]["data"]["code"] == "schema_validation_failed"


def test_stream_sse_wire_uses_event_and_data_lines(client_factory):
    """课程口径线缆格式：event: <name> 行 + data: <json> 行，事件名不进 data。"""
    pro = FakeProvider(chunks=["one ", "two"])
    client = client_factory(pro=pro)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": []}) as response:
        body = response.read().decode("utf-8")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: content.delta\n" in body
    assert "event: content.done\n" in body
    frames = [f for f in body.split("\n\n") if f.strip()]
    first_lines = [ln for ln in frames[0].split("\n") if ln]
    assert first_lines[0] == "event: content.delta"
    assert first_lines[1].startswith("data: ")
    payload = json.loads(first_lines[1][len("data: "):])
    assert payload["delta"] == "one "          # 数据负载
    assert "event" not in payload              # 事件名只存在于 event: 行


def test_stream_chain_exhausted_before_first_chunk_emits_fallback_exhausted(client_factory):
    # 主模型 1+3 次重试均失败、备用模型同样预算用尽（首块均未发出）
    pro = FakeProvider(stream_failures=[retryable_error(f"p{i}") for i in range(4)])
    flash = FakeProvider(stream_failures=[retryable_error(f"f{i}") for i in range(4)])
    client = client_factory(pro=pro, flash=flash)
    with _stream(client, {"model": "deepseek-v4-pro", "messages": []}) as response:
        events = sse_events(response)
    assert [e["event"] for e in events] == ["response.failed"]
    assert events[0]["data"]["code"] == "fallback_exhausted"
    trace = client.get("/trace").json()["traces"][0]
    assert trace["attempts"] == 8
    assert trace["error_code"] == "fallback_exhausted"
