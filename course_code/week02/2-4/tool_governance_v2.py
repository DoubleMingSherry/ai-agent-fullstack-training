from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    tool_name: str
    ok: bool
    content: Mapping[str, Any]
    error_code: str | None = None

    def to_model_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    trace_id: str
    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    allowed_tools: frozenset[str]


ToolHandler = Callable[[ToolArguments, ExecutionContext], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    parameters_model: type[ToolArguments]
    handler: ToolHandler


class PermissionEngine:
    def decide(self, tool: ToolDefinition, ctx: ExecutionContext) -> PermissionDecision:
        if tool.name not in ctx.allowed_tools:
            return PermissionDecision.DENY
        if tool.name not in ctx.permissions:
            return PermissionDecision.DENY
        return PermissionDecision.ALLOW


@dataclass(slots=True)
class AuditSink:
    records: list[dict[str, Any]] = field(default_factory=list)

    async def write(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))


@dataclass(frozen=True, slots=True)
class PreparedCall:
    call: ToolCall
    tool: ToolDefinition
    arguments: ToolArguments


SENSITIVE_KEY = re.compile(r"token|secret|password|authorization|api_?key", re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "***" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "***@***", value)
    return value


class ToolRuntime:
    def __init__(
        self,
        tools: Sequence[ToolDefinition],
        permissions: PermissionEngine,
        audit: AuditSink,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("工具名称必须唯一")
        self._permissions = permissions
        self._audit = audit

    async def invoke(self, call: ToolCall, ctx: ExecutionContext) -> ToolResult:
        started_at = time.monotonic()
        prepared_or_result = await self.prepare(call, ctx)
        if isinstance(prepared_or_result, ToolResult):
            return await self.finalize(call, ctx, prepared_or_result, started_at)

        prepared = prepared_or_result
        try:
            raw_content = await self.execute(prepared, ctx)
        except Exception:
            failed = ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                content={"message": "工具执行失败，请联系人工处理"},
                error_code="TOOL_ERROR",
            )
            return await self.finalize(call, ctx, failed, started_at)

        succeeded = ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            ok=True,
            content=dict(raw_content),
        )
        return await self.finalize(call, ctx, succeeded, started_at)

    async def prepare(self, call: ToolCall, ctx: ExecutionContext) -> PreparedCall | ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return self._failure(call, "TOOL_NOT_FOUND", "工具未注册")
        if not isinstance(call.arguments, Mapping):
            return self._failure(call, "INVALID_ARGUMENT", "工具参数必须是对象")
        try:
            arguments = tool.parameters_model.model_validate(call.arguments)
        except ValidationError:
            return self._failure(call, "INVALID_ARGUMENT", "工具参数校验失败")

        decision = self._permissions.decide(tool, ctx)
        if decision is PermissionDecision.DENY:
            return self._failure(call, "PERMISSION_DENIED", "当前主体无权调用该工具")
        if decision is PermissionDecision.CONFIRM:
            return self._failure(call, "CONFIRMATION_REQUIRED", "该操作需要用户确认")
        return PreparedCall(call=call, tool=tool, arguments=arguments)

    async def execute(self, prepared: PreparedCall, ctx: ExecutionContext) -> Mapping[str, Any]:
        return await self._execute_with_recovery(prepared, ctx)

    async def _execute_with_recovery(
        self,
        prepared: PreparedCall,
        ctx: ExecutionContext,
    ) -> Mapping[str, Any]:
        return await prepared.tool.handler(prepared.arguments, ctx)

    async def finalize(
        self,
        call: ToolCall,
        ctx: ExecutionContext,
        result: ToolResult,
        started_at: float,
    ) -> ToolResult:
        safe_result = ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            ok=result.ok,
            content=redact(result.content),
            error_code=result.error_code,
        )
        await self._audit.write(
            {
                "trace_id": ctx.trace_id,
                "tool_call_id": safe_result.tool_call_id,
                "tool_name": safe_result.tool_name,
                "ok": safe_result.ok,
                "error_code": safe_result.error_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
            }
        )
        return safe_result

    @staticmethod
    def _failure(call: ToolCall, code: str, message: str) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            ok=False,
            content={"message": message},
            error_code=code,
        )


class AgentLoop:
    def __init__(self, runtime: ToolRuntime) -> None:
        self._runtime = runtime

    async def run_tool_calls(
        self,
        calls: Sequence[ToolCall],
        ctx: ExecutionContext,
    ) -> list[dict[str, Any]]:
        results = [await self._runtime.invoke(call, ctx) for call in calls]
        return [result.to_model_message() for result in results]
