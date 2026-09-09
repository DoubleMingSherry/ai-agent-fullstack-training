"""Shared offline test fixtures.

Every test talks to the FastAPI app through httpx ASGITransport via Starlette's
TestClient — no network, no credentials, no vendor SDK client is ever created.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.trace import TraceStore  # noqa: E402

from fakes import FakeProvider  # noqa: E402


def build_client(
    pro: Optional[FakeProvider] = None,
    flash: Optional[FakeProvider] = None,
    *,
    rate_limits: Optional[Dict[str, int]] = None,
    backoff_base: float = 0.0,  # acceptance: tests inject 0 backoff
    max_retries: int = 3,
    max_input_tokens: Optional[int] = None,
    trace_store: Optional[TraceStore] = None,
) -> TestClient:
    pro = pro if pro is not None else FakeProvider(text="answer from pro")
    flash = flash if flash is not None else FakeProvider(text="answer from flash")
    app = create_app(
        providers={"deepseek-v4-pro": pro, "deepseek-v4-flash": flash},
        rate_limits=rate_limits,
        backoff_base=backoff_base,
        max_retries=max_retries,
        max_input_tokens=max_input_tokens,
        trace_store=trace_store,
    )
    return TestClient(app)


def sse_events(response: Any) -> List[Dict[str, Any]]:
    """Parse a text/event-stream body into [{"event": name, "data": payload}, ...].

    Wire format (course): one ``event: <name>`` line + one ``data: <json>``
    line per frame — the event name never rides inside the JSON payload.
    """
    events: List[Dict[str, Any]] = []
    name: Optional[str] = None
    for line in response.iter_lines():
        line = line.rstrip("\r")
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = json.loads(line[len("data:"):].strip())
            events.append({"event": name, "data": payload})
            name = None
    return events


@pytest.fixture
def client_factory() -> Callable[..., TestClient]:
    return build_client


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    return client_factory()
