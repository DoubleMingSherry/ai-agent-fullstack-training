from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTicketInput(StrictModel):
    title: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    priority: Literal["low", "medium", "high"] = "medium"


class CreateTicketOutput(StrictModel):
    ticket_id: str
    status: Literal["created"]


class ToolCall(StrictModel):
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    approved_call_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    retry_on_timeout: bool = False


ToolHandler = Callable[[BaseModel, ExecutionContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    permission: str
    risk: Literal["low", "medium", "high"]
    timeout_s: float
    retry: RetryPolicy
    error_codes: tuple[str, ...]
    audit_fields: tuple[str, ...]
    category: Literal["database", "http", "file", "external"]
    access: Literal["read", "write"]
    dependencies: tuple[str, ...] = ()
    execution_mode: Literal["sequential", "parallel"] = "parallel"

    def to_provider_tool(self) -> dict[str, Any]:
        # 权限、风险、handler 等治理字段不会发给模型。
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


@dataclass(frozen=True)
class ToolSnapshot:
    tools: Mapping[str, ToolDefinition]

    def provider_tools(self) -> list[dict[str, Any]]:
        return [tool.to_provider_tool() for tool in self.tools.values()]


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[dict[str, str]]
    details: dict[str, Any]
    is_error: bool
    error: dict[str, Any] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        key = (tool.name, tool.version)
        if key in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}@{tool.version}")
        if tool.timeout_s <= 0 or tool.retry.max_attempts < 1:
            raise ValueError("timeout and retry policy must be positive")
        self._tools[key] = tool

    def snapshot(
        self,
        routes: Mapping[str, str],
        enabled: Mapping[str, bool],
    ) -> ToolSnapshot:
        selected: dict[str, ToolDefinition] = {}
        for name, version in routes.items():
            if not enabled.get(name, True):
                continue
            key = (name, version)
            if key not in self._tools:
                raise KeyError(f"tool not registered: {name}@{version}")
            selected[name] = self._tools[key]
        return ToolSnapshot(MappingProxyType(selected))


class ToolRuntime:
    def __init__(
        self,
        enabled: dict[str, bool],
        healthy_dependencies: set[str],
        audit_log: list[dict[str, Any]],
    ) -> None:
        self.enabled = enabled
        self.healthy_dependencies = healthy_dependencies
        self.audit_log = audit_log

    async def invoke(
        self,
        snapshot: ToolSnapshot,
        call: ToolCall,
        context: ExecutionContext,
    ) -> ToolResultMessage:
        # prepare：查找、启停检查、参数校验、权限和依赖检查。
        tool = snapshot.tools.get(call.name)
        if tool is None:
            return self._error(call, context, None, "TOOL_NOT_FOUND")
        if not self.enabled.get(call.name, True):
            return self._error(call, context, tool, "TOOL_DISABLED")

        try:
            params = tool.input_model.model_validate(call.arguments)
        except ValidationError:
            return self._error(call, context, tool, "INVALID_ARGUMENT")

        if tool.permission not in context.permissions:
            return self._error(call, context, tool, "PERMISSION_DENIED")
        if tool.risk == "high" and call.id not in context.approved_call_ids:
            return self._error(call, context, tool, "APPROVAL_REQUIRED")
        if any(name not in self.healthy_dependencies for name in tool.dependencies):
            return self._error(call, context, tool, "DEPENDENCY_UNAVAILABLE")

        # execute：只有 prepare 全部通过，handler 才能运行。
        for attempt in range(1, tool.retry.max_attempts + 1):
            try:
                raw_output = await asyncio.wait_for(
                    tool.handler(params, context),
                    timeout=tool.timeout_s,
                )
                break
            except TimeoutError:
                if not tool.retry.retry_on_timeout or attempt == tool.retry.max_attempts:
                    return self._error(call, context, tool, "TIMEOUT")

        # finalize：校验输出、审计，并包装成能写回 Agent Loop 的消息。
        try:
            output = tool.output_model.model_validate(raw_output)
        except ValidationError:
            return self._error(call, context, tool, "INVALID_OUTPUT")

        self._audit(call, context, tool, "OK")
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[
                {
                    "type": "text",
                    "text": json.dumps(
                        output.model_dump(), ensure_ascii=False, sort_keys=True
                    ),
                }
            ],
            details={"version": tool.version, "attempt": attempt},
            is_error=False,
        )

    def _error(
        self,
        call: ToolCall,
        context: ExecutionContext,
        tool: ToolDefinition | None,
        code: str,
    ) -> ToolResultMessage:
        if tool and code not in tool.error_codes:
            raise ValueError(f"undeclared error code: {code}")
        self._audit(call, context, tool, code)
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[{"type": "text", "text": code}],
            details={"version": tool.version if tool else None},
            is_error=True,
            error={"code": code, "retryable": code == "TIMEOUT"},
        )

    def _audit(
        self,
        call: ToolCall,
        context: ExecutionContext,
        tool: ToolDefinition | None,
        outcome: str,
    ) -> None:
        self.audit_log.append(
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "version": tool.version if tool else None,
                "tenant_id": context.tenant_id,
                "user_id": context.user_id,
                "outcome": outcome,
                "argument_fields": sorted(
                    name
                    for name in call.arguments
                    if tool and name in tool.audit_fields
                ),
            }
        )


async def create_ticket(
    params: BaseModel,
    context: ExecutionContext,
) -> dict[str, Any]:
    CreateTicketInput.model_validate(params)
    await asyncio.sleep(0.01)  # 模拟内部 HTTP API
    return {"ticket_id": "T-1001", "status": "created"}


def show(result: ToolResultMessage) -> None:
    outcome = result.error["code"] if result.error else f"OK {result.content[0]['text']}"
    print(f"{result.tool_call_id} -> {outcome}")


async def main() -> None:
    enabled = {"create_ticket": True}
    healthy_dependencies = {"ticket-service", "ticket-api-key"}
    audit_log: list[dict[str, Any]] = []

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="create_ticket",
            version="1.0.0",
            description="创建客服工单；调用前需要写权限和人工批准",
            input_model=CreateTicketInput,
            output_model=CreateTicketOutput,
            handler=create_ticket,
            permission="ticket:write",
            risk="high",
            timeout_s=1.0,
            retry=RetryPolicy(max_attempts=1),
            error_codes=(
                "INVALID_ARGUMENT",
                "PERMISSION_DENIED",
                "APPROVAL_REQUIRED",
                "DEPENDENCY_UNAVAILABLE",
                "TOOL_DISABLED",
                "TIMEOUT",
                "INVALID_OUTPUT",
            ),
            audit_fields=("title", "priority"),
            category="http",
            access="write",
            dependencies=("ticket-service", "ticket-api-key"),
            execution_mode="sequential",
        )
    )

    # 工单处理模式只激活明确选择的 name@version。
    snapshot = registry.snapshot({"create_ticket": "1.0.0"}, enabled)
    visible = [item["function"]["name"] for item in snapshot.provider_tools()]
    print("模型可见工具:", ", ".join(visible))

    runtime = ToolRuntime(enabled, healthy_dependencies, audit_log)
    arguments = {"title": "退款失败", "description": "请人工跟进"}
    cases = [
        (
            ToolCall(id="call-1", name="create_ticket", arguments={"title": "短"}),
            ExecutionContext("u-1", "tenant-a", frozenset({"ticket:write"})),
        ),
        (
            ToolCall(id="call-2", name="create_ticket", arguments=arguments),
            ExecutionContext("u-1", "tenant-a", frozenset()),
        ),
        (
            ToolCall(id="call-3", name="create_ticket", arguments=arguments),
            ExecutionContext("u-1", "tenant-a", frozenset({"ticket:write"})),
        ),
        (
            ToolCall(id="call-4", name="create_ticket", arguments=arguments),
            ExecutionContext(
                "u-1",
                "tenant-a",
                frozenset({"ticket:write"}),
                frozenset({"call-4"}),
            ),
        ),
    ]

    for call, context in cases:
        show(await runtime.invoke(snapshot, call, context))

    # 快照创建后临时下线，执行前的第二次检查仍能阻止旧 Tool Call。
    enabled["create_ticket"] = False
    disabled_call = ToolCall(id="call-5", name="create_ticket", arguments=arguments)
    disabled_context = ExecutionContext(
        "u-1",
        "tenant-a",
        frozenset({"ticket:write"}),
        frozenset({"call-5"}),
    )
    show(await runtime.invoke(snapshot, disabled_call, disabled_context))
    print("审计记录数:", len(audit_log))


if __name__ == "__main__":
    asyncio.run(main())
