"""Acceptance 10: no vendor API key / Base URL anywhere in the example caller
and its tests — nor in the whole gateway source tree (credentials are env-only).
Also an offline end-to-end run of the example MiniAgent against the gateway.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from gateway.app import create_app  # noqa: E402
from gateway.trace import TraceStore  # noqa: E402
from mini_agent import MiniAgent  # noqa: E402

from fakes import FakeProvider  # noqa: E402

# ---------------------------------------------------------------------------
# secret hygiene
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),                 # OpenAI-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),             # Anthropic-style keys
    re.compile(r"api[_-]?key\s*=\s*[\"'][^\"']{6,}[\"']", re.IGNORECASE),  # literal key assignment
    re.compile(r"authorization\s*[:=]\s*[\"'][^\"']{10,}[\"']", re.IGNORECASE),
    re.compile(r"https?://(?:[^\"'\s]*\.)?(?:openai|anthropic|deepseek)\.[a-z.]+"),  # vendor URL
]

PY_FILES = [
    p
    for p in ROOT.rglob("*.py")
    if "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
]


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_vendor_secret_or_base_url_in_source(path):
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(line), (
                f"{path.relative_to(ROOT)}:{index} looks like a hard-coded secret/URL: {line.strip()}"
            )


def test_agent_module_has_no_secret_strings():
    source = open(pathlib.Path(ROOT / "examples" / "mini_agent.py"), encoding="utf-8").read()
    assert "api_key" not in source.lower()
    assert all(p.search(source) is None for p in SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# offline end-to-end with the example caller
# ---------------------------------------------------------------------------
def test_mini_agent_talks_to_gateway_offline():
    pro = FakeProvider(text="agent got: pro answer", chunks=["agent got: ", "pro answer"])
    flash = FakeProvider(text="flash answer")
    trace_store = TraceStore()
    app = create_app(
        providers={"deepseek-v4-pro": pro, "deepseek-v4-flash": flash},
        trace_store=trace_store,
    )

    async def scenario() -> None:
        agent = MiniAgent(caller_id="e2e-agent", transport=httpx.ASGITransport(app=app))
        try:
            # plain chat through the gateway HTTP interface only
            result = await agent.chat(
                "deepseek-v4-pro", [{"role": "user", "content": "hello"}]
            )
            assert result["text"] == "agent got: pro answer"
            assert result["model_used"] == "deepseek-v4-pro"

            # stream through the gateway HTTP interface only
            chunks: list = []
            async for event in agent.stream("deepseek-v4-flash", []):
                if event["event"] == "content.delta":
                    chunks.append(event["data"]["delta"])
                if event["event"] == "content.done":
                    assert event["data"]["model_used"] == "deepseek-v4-flash"
            assert "".join(chunks) == "flash answer"

            # template reference through the gateway
            templated = await agent.chat(
                "deepseek-v4-flash",
                [],
                prompt={"name": "chat", "version": 2,
                        "variables": {"role": "guide", "domain": "museums", "style": "brief"}},
            )
            assert templated["prompt"]["name"] == "chat"
        finally:
            await agent.close()

    asyncio.run(scenario())

    # caller id propagated into traces
    records = trace_store.list_traces(caller_id="e2e-agent")
    assert len(records) == 3
    assert all(r["caller_id"] == "e2e-agent" for r in records)
