import asyncio
import json

from tool_runtime_demo_v2 import (
    ExecutionContext,
    ToolCall,
    ToolRuntime,
    build_registry,
    to_provider_tool_message,
)


def make_runtime(enabled: dict[str, bool] | None = None) -> tuple[ToolRuntime, object]:
    enabled = enabled or {"query_weather": True, "query_customer_database": True}
    registry = build_registry()
    snapshot = registry.snapshot(
        {"query_weather": "1.0.0", "query_customer_database": "1.0.0"}, enabled
    )
    return ToolRuntime(enabled, {"weather-service", "customer-database"}), snapshot


def test_weather_tool_returns_result_and_preserves_call_id() -> None:
    runtime, snapshot = make_runtime()
    context = ExecutionContext("u-1", "t-1", frozenset({"weather:read"}))
    result = asyncio.run(
        runtime.invoke(
            snapshot, ToolCall(id="call-weather", name="query_weather", arguments={"city": "北京"}), context
        )
    )

    assert result.is_error is False
    assert result.tool_call_id == "call-weather"
    assert json.loads(result.content)["city"] == "北京"
    assert to_provider_tool_message(result)["tool_call_id"] == "call-weather"


def test_invalid_arguments_and_permission_are_structured_errors() -> None:
    runtime, snapshot = make_runtime()
    no_permission = ExecutionContext("u-1", "t-1", frozenset())
    invalid = asyncio.run(
        runtime.invoke(
            snapshot, ToolCall(id="call-invalid", name="query_weather", arguments={"unknown": 1}), no_permission
        )
    )
    denied = asyncio.run(
        runtime.invoke(
            snapshot,
            ToolCall(id="call-denied", name="query_weather", arguments={"city": "上海"}),
            no_permission,
        )
    )

    assert invalid.error and invalid.error.code == "INVALID_ARGUMENT"
    assert denied.error and denied.error.code == "PERMISSION_DENIED"


def test_runtime_disable_stops_an_old_snapshot() -> None:
    enabled = {"query_weather": True, "query_customer_database": True}
    runtime, snapshot = make_runtime(enabled)
    enabled["query_weather"] = False
    context = ExecutionContext("u-1", "t-1", frozenset({"weather:read"}))
    result = asyncio.run(
        runtime.invoke(
            snapshot, ToolCall(id="call-off", name="query_weather", arguments={"city": "深圳"}), context
        )
    )

    assert result.error and result.error.code == "TOOL_DISABLED"
