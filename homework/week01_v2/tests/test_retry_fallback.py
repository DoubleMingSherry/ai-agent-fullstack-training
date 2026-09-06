"""Acceptance 6: retry with exponential backoff (0 injected in tests), up to 3
retries per model, then a capability-equivalent fallback; when the backup's
budget is also exhausted the gateway returns a clear error and stops switching.
"""

from __future__ import annotations

import asyncio

from gateway.app import create_app
from gateway.models import ChatRequest
from gateway.ratelimit import RateLimiter
from gateway.registry import builtin_registry
from gateway.service import GatewayService
from gateway.templates import builtin_templates
from gateway.trace import TraceStore

from fakes import FakeProvider, fatal_error, retryable_error


def _chat(client, model: str = "deepseek-v4-pro", caller: str = "retry-tester") -> dict:
    return client.post(
        "/chat",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Caller-Id": caller},
    )


def test_acceptance6_retry_then_fallback_succeeds(client_factory):
    pro = FakeProvider(
        text="pro",
        complete_failures=[retryable_error(f"fail-{i}") for i in range(4)],  # 1 + 3 retries
    )
    flash = FakeProvider(text="backup answer")
    client = client_factory(pro=pro, flash=flash)
    response = _chat(client)
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "backup answer"
    assert body["model_used"] == "deepseek-v4-flash"  # invariant 3 evidence
    assert body["attempts"] == 5  # 4 pro attempts (1 + 3 retries) + 1 flash
    assert pro.complete_calls == 4
    assert flash.complete_calls == 1


def test_acceptance6b_backup_budget_exhausted_clear_error_no_more_switching(client_factory):
    pro = FakeProvider(complete_failures=[retryable_error(f"m{i}") for i in range(4)])
    flash = FakeProvider(complete_failures=[retryable_error(f"b{i}") for i in range(4)])
    client = client_factory(pro=pro, flash=flash)
    response = _chat(client)
    assert response.status_code == 502
    # 链上全部失败：配置了备用模型 → 明确的 fallback_exhausted
    assert response.json()["error"]["code"] == "fallback_exhausted"
    assert pro.complete_calls == 4 and flash.complete_calls == 4
    # the chain stopped: no further attempts happened beyond the backup budget
    trace = client.get("/trace").json()["traces"][0]
    assert trace["attempts"] == 8
    assert trace["status"] == "error"
    assert trace["error_code"] == "fallback_exhausted"


def test_main_recovers_inside_retry_budget_no_fallback(client_factory):
    pro = FakeProvider(text="recovered", complete_failures=[retryable_error(), retryable_error()])
    flash = FakeProvider(text="unused")
    client = client_factory(pro=pro, flash=flash)
    response = _chat(client)
    assert response.status_code == 200
    assert response.json()["text"] == "recovered"
    assert response.json()["attempts"] == 3
    assert response.json()["model_used"] == "deepseek-v4-pro"
    assert flash.complete_calls == 0


def test_non_retryable_failure_skips_retry_and_falls_back(client_factory):
    pro = FakeProvider(complete_failures=[fatal_error("bad request upstream")])
    flash = FakeProvider(text="backup")
    client = client_factory(pro=pro, flash=flash)
    response = _chat(client)
    assert response.status_code == 200
    assert response.json()["model_used"] == "deepseek-v4-flash"
    assert response.json()["attempts"] == 2  # fatal: no retries, straight fallback
    assert pro.complete_calls == 1 and flash.complete_calls == 1


def test_non_retryable_no_backup_fails_clearly(client_factory):
    flash = FakeProvider(complete_failures=[fatal_error("boom")])
    client = client_factory(flash=flash)
    response = _chat(client, model="deepseek-v4-flash")  # no fallback declared
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_backoff_delays_are_exponential_when_injected():
    """Backoff is configurable: gateway sleeps base*2^n before each retry."""
    delays: list = []

    async def capture_sleep(seconds: float) -> None:
        delays.append(seconds)

    pro = FakeProvider(
        text="recovered", complete_failures=[retryable_error() for _ in range(3)]
    )
    service = GatewayService(
        registry=builtin_registry(),
        templates=builtin_templates(),
        providers={"deepseek-v4-pro": pro, "deepseek-v4-flash": FakeProvider("ok")},
        trace_store=TraceStore(),
        rate_limiter=RateLimiter({"deepseek-v4-pro": 60, "deepseek-v4-flash": 60}),
        backoff_base=1.0,
        max_retries=3,
        sleep=capture_sleep,
    )

    async def run() -> None:
        await service.execute_text(
            service.begin(ChatRequest(model="deepseek-v4-pro", messages=[]))
        )

    asyncio.run(run())
    # attempt1 fails -> sleep 1.0*2^0; attempt2 fails -> 1.0*2^1; attempt3 fails -> 1.0*2^2
    assert delays == [1.0, 2.0, 4.0]
    assert pro.complete_calls == 4
