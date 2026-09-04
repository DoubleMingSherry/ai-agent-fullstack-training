"""验收 10：示例调用方只依赖网关协议——源码无供应商凭证，
且通过 ASGITransport 端到端验证统一协议。"""

import asyncio
from pathlib import Path

import httpx

from caller.business_agent import BusinessAgent, GatewayCallError
from conftest import FakeProvider, make_app

CALLER_SOURCE = Path(__file__).resolve().parent.parent / "caller" / "business_agent.py"


def test_caller_source_has_no_provider_credentials():
    """调用方代码不出现供应商 API Key 和 Base URL。"""
    source = CALLER_SOURCE.read_text(encoding="utf-8").lower()
    for marker in ("api_key", "api.deepseek.com", "sk-", "authorization", "provider"):
        assert marker not in source, f"调用方源码不应出现: {marker}"


def test_business_agent_end_to_end_over_gateway_protocol():
    provider = FakeProvider(
        [
            {"kind": "text", "text": "同一操作执行多次结果一致。"},
            {"kind": "text", "text": '{"category": "refund", "confidence": 0.9}'},
        ]
    )
    app = make_app(provider)
    transport = httpx.ASGITransport(app=app)
    agent = BusinessAgent(gateway_url="http://gw.test", transport=transport)

    answer = asyncio.run(agent.answer("什么是幂等？"))
    assert answer == "同一操作执行多次结果一致。"

    data = asyncio.run(agent.classify("我要退货退款"))
    assert data == {"category": "refund", "confidence": 0.9}


def test_business_agent_maps_error_envelope():
    app = make_app(FakeProvider([]))
    transport = httpx.ASGITransport(app=app)
    agent = BusinessAgent(gateway_url="http://gw.test", transport=transport)

    try:
        asyncio.run(agent.answer("什么是幂等？", model="ghost-model"))
        raise AssertionError("应当抛出 GatewayCallError")
    except GatewayCallError as exc:
        assert exc.code == "unknown_model"
        assert exc.call_id.startswith("call-")
