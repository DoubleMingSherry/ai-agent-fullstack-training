from __future__ import annotations

from typing import Any

import pytest
from pydantic import Field

from tool_governance_v2 import (
    AuditSink,
    ExecutionContext,
    PermissionDecision,
    PermissionEngine,
    ToolArguments,
    ToolCall,
    ToolDefinition,
    ToolRuntime,
)


class GetOrderArguments(ToolArguments):
    order_id: str = Field(pattern=r"^ord_\d{4}$")


class FixedPermissionEngine(PermissionEngine):
    def __init__(self, decision: PermissionDecision) -> None:
        self._decision = decision

    def decide(self, tool: ToolDefinition, ctx: ExecutionContext) -> PermissionDecision:
        return self._decision


def context() -> ExecutionContext:
    return ExecutionContext(
        trace_id="trace_01",
        user_id="user_01",
        tenant_id="tenant_01",
        permissions=frozenset({"get_order"}),
        allowed_tools=frozenset({"get_order"}),
    )


@pytest.mark.asyncio
async def test_allow_executes_then_projects_and_audits() -> None:
    async def handler(_args: ToolArguments, _ctx: ExecutionContext) -> dict[str, Any]:
        return {"customer_email": "alice@example.com", "access_token": "secret"}

    audit = AuditSink()
    runtime = ToolRuntime(
        [ToolDefinition("get_order", GetOrderArguments, handler)],
        FixedPermissionEngine(PermissionDecision.ALLOW),
        audit,
    )

    result = await runtime.invoke(ToolCall("call_01", "get_order", {"order_id": "ord_1001"}), context())

    assert result.ok is True
    assert result.tool_call_id == "call_01"
    assert result.content == {"customer_email": "***@***", "access_token": "***"}
    assert audit.records[-1]["tool_call_id"] == "call_01"


@pytest.mark.asyncio
async def test_deny_short_circuits_before_handler() -> None:
    calls = 0

    async def handler(_args: ToolArguments, _ctx: ExecutionContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {}

    runtime = ToolRuntime(
        [ToolDefinition("get_order", GetOrderArguments, handler)],
        FixedPermissionEngine(PermissionDecision.DENY),
        AuditSink(),
    )

    result = await runtime.invoke(ToolCall("call_02", "get_order", {"order_id": "ord_1001"}), context())

    assert result.error_code == "PERMISSION_DENIED"
    assert result.tool_call_id == "call_02"
    assert calls == 0


@pytest.mark.asyncio
async def test_confirm_short_circuits_before_handler() -> None:
    calls = 0

    async def handler(_args: ToolArguments, _ctx: ExecutionContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {}

    runtime = ToolRuntime(
        [ToolDefinition("get_order", GetOrderArguments, handler)],
        FixedPermissionEngine(PermissionDecision.CONFIRM),
        AuditSink(),
    )

    result = await runtime.invoke(ToolCall("call_03", "get_order", {"order_id": "ord_1001"}), context())

    assert result.error_code == "CONFIRMATION_REQUIRED"
    assert result.tool_call_id == "call_03"
    assert calls == 0
