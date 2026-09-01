from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tool_runtime")


class StrictModel(BaseModel):
    """所有跨越 Runtime 边界的数据都禁止携带未声明字段。"""

    model_config = ConfigDict(extra="forbid")


class WeatherQueryInput(StrictModel):
    """模型唯一能够传给天气工具的参数契约。"""

    city: str = Field(min_length=1, max_length=40, description="要查询天气的城市")
    unit: Literal["celsius", "fahrenheit"] = Field(
        default="celsius", description="温度单位"
    )


class WeatherQueryOutput(StrictModel):
    city: str
    condition: str
    temperature: float
    unit: Literal["celsius", "fahrenheit"]


class DatabaseQueryInput(StrictModel):
    """使用受限查询条件而不是让模型传递任意 SQL，避免 SQL 注入边界失效。"""

    customer_id: str = Field(
        min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$", description="客户编号"
    )


class DatabaseQueryOutput(StrictModel):
    customer_id: str
    name: str
    membership: Literal["basic", "gold", "platinum"]
    order_count: int = Field(ge=0)


class ToolCall(StrictModel):
    """从 Provider 响应解析出的供应商无关 Tool Call。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolError(StrictModel):
    """发送给 LLM 的稳定错误协议，方便其决定是否改参或向用户说明。"""

    code: str
    message: str
    retryable: bool = False


class ToolResultMessage(StrictModel):
    """Runtime 的统一产物，tool_call_id 会原样回传给 Provider。"""

    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool
    error: ToolError | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionContext:
    """由业务系统建立的可信上下文，绝不能由 LLM 参数构造。"""

    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    approved_call_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ToolSchema:
    """只保存模型可见的名称、描述和 JSON Schema，不含 Handler 与治理策略。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_provider_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Handler(Protocol):
    """Handler 只由 Runtime 调用；该对象永远不序列化给模型。"""

    async def __call__(
        self, params: BaseModel, context: ExecutionContext
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ToolDefinition:
    """注册表内的完整工具定义：Schema 对模型开放，其余字段只服务于治理和执行。"""

    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Handler
    permission: str
    risk: Literal["low", "medium", "high"]
    timeout_s: float
    dependencies: tuple[str, ...] = ()

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
        )


@dataclass(frozen=True)
class ToolSnapshot:
    """一次 Agent 请求固定使用的只读工具视图，防止中途路由漂移。"""

    tools: Mapping[str, ToolDefinition]

    def provider_tools(self) -> list[dict[str, Any]]:
        return [tool.schema().to_provider_tool() for tool in self.tools.values()]


class ToolRegistry:
    """持有多个工具版本，并依据路由和启用开关生成快照。"""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name or not tool.version or tool.timeout_s <= 0:
            raise ValueError("tool name, version and positive timeout are required")
        key = (tool.name, tool.version)
        if key in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}@{tool.version}")
        self._tools[key] = tool

    def snapshot(
        self, routes: Mapping[str, str], enabled: Mapping[str, bool]
    ) -> ToolSnapshot:
        selected: dict[str, ToolDefinition] = {}
        for name, version in routes.items():
            if not enabled.get(name, True):
                continue
            tool = self._tools.get((name, version))
            if tool is None:
                raise KeyError(f"tool not registered: {name}@{version}")
            selected[name] = tool
        return ToolSnapshot(MappingProxyType(selected))


class ToolRuntime:
    """执行唯一入口：检查、调用、结果校验、结构化错误及审计都在这里完成。"""

    def __init__(
        self,
        enabled: Mapping[str, bool],
        healthy_dependencies: set[str],
    ) -> None:
        self._enabled = enabled
        self._healthy_dependencies = healthy_dependencies

    async def invoke(
        self,
        snapshot: ToolSnapshot,
        call: ToolCall,
        context: ExecutionContext,
    ) -> ToolResultMessage:
        tool = snapshot.tools.get(call.name)
        if tool is None:
            return self._error(call, None, "TOOL_NOT_FOUND", "工具不在当前快照中")
        if not self._enabled.get(tool.name, True):
            return self._error(call, tool, "TOOL_DISABLED", "工具当前已停用")

        try:
            params = tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._error(
                call,
                tool,
                "INVALID_ARGUMENT",
                "工具参数不符合 Schema",
                {"validation_errors": exc.error_count()},
            )

        if tool.permission not in context.permissions:
            return self._error(call, tool, "PERMISSION_DENIED", "调用方没有所需权限")
        if tool.risk == "high" and call.id not in context.approved_call_ids:
            return self._error(call, tool, "APPROVAL_REQUIRED", "高风险调用需要人工批准")
        if any(dep not in self._healthy_dependencies for dep in tool.dependencies):
            return self._error(
                call, tool, "DEPENDENCY_UNAVAILABLE", "工具依赖当前不可用", retryable=True
            )

        try:
            raw_output = await asyncio.wait_for(
                tool.handler(params, context), timeout=tool.timeout_s
            )
        except TimeoutError:
            return self._error(call, tool, "TIMEOUT", "工具调用超时", retryable=True)
        except Exception:
            logger.exception("tool_handler_failed tool=%s call_id=%s", tool.name, call.id)
            return self._error(call, tool, "EXECUTION_FAILED", "工具执行失败")

        try:
            output = tool.output_model.model_validate(raw_output)
        except ValidationError:
            logger.error("tool_output_invalid tool=%s call_id=%s", tool.name, call.id)
            return self._error(call, tool, "INVALID_OUTPUT", "工具返回结果不符合 Schema")

        result = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=tool.name,
            content=json.dumps(output.model_dump(), ensure_ascii=False),
            is_error=False,
            details={"version": tool.version, "risk": tool.risk},
        )
        self._log_result(context, result)
        return result

    def _error(
        self,
        call: ToolCall,
        tool: ToolDefinition | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> ToolResultMessage:
        result = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=json.dumps(
                {"code": code, "message": message}, ensure_ascii=False
            ),
            is_error=True,
            error=ToolError(code=code, message=message, retryable=retryable),
            details={"version": tool.version if tool else None, **(details or {})},
        )
        logger.warning("tool_error tool=%s call_id=%s code=%s", call.name, call.id, code)
        return result

    def _log_result(self, context: ExecutionContext, result: ToolResultMessage) -> None:
        logger.info(
            "tool_success tool=%s call_id=%s user=%s tenant=%s",
            result.tool_name,
            result.tool_call_id,
            context.user_id,
            context.tenant_id,
        )


async def query_weather(
    params: BaseModel, _context: ExecutionContext
) -> Mapping[str, Any]:
    """示例 Handler；实际项目中可替换为受控的天气服务客户端。"""

    request = WeatherQueryInput.model_validate(params)
    temperatures = {"北京": (26.0, "晴"), "上海": (29.0, "多云"), "深圳": (31.0, "小雨")}
    temperature, condition = temperatures.get(request.city, (22.0, "晴间多云"))
    if request.unit == "fahrenheit":
        temperature = round(temperature * 9 / 5 + 32, 1)
    return {
        "city": request.city,
        "condition": condition,
        "temperature": temperature,
        "unit": request.unit,
    }


async def query_customer_database(
    params: BaseModel, _context: ExecutionContext
) -> Mapping[str, Any]:
    """示例数据库 Handler；固定参数化查询边界，未向模型开放 SQL。"""

    request = DatabaseQueryInput.model_validate(params)
    customers = {
        "cust-1001": {"name": "王小明", "membership": "gold", "order_count": 12},
        "cust-1002": {"name": "李小红", "membership": "platinum", "order_count": 28},
    }
    customer = customers.get(
        request.customer_id,
        {"name": "未登记客户", "membership": "basic", "order_count": 0},
    )
    return {"customer_id": request.customer_id, **customer}


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="query_weather",
            version="1.0.0",
            description="查询指定城市的当前示例天气",
            input_model=WeatherQueryInput,
            output_model=WeatherQueryOutput,
            handler=query_weather,
            permission="weather:read",
            risk="low",
            timeout_s=3.0,
            dependencies=("weather-service",),
        )
    )
    registry.register(
        ToolDefinition(
            name="query_customer_database",
            version="1.0.0",
            description="按客户编号查询已授权的客户摘要信息",
            input_model=DatabaseQueryInput,
            output_model=DatabaseQueryOutput,
            handler=query_customer_database,
            permission="database:read",
            risk="medium",
            timeout_s=3.0,
            dependencies=("customer-database",),
        )
    )
    return registry


def parse_tool_calls(message: Any) -> list[ToolCall]:
    """将 DeepSeek 的 OpenAI 兼容响应转换为 Runtime 的内部调用对象。"""

    parsed_calls: list[ToolCall] = []
    for call in message.tool_calls or []:
        try:
            arguments = json.loads(call.function.arguments)
            parsed_calls.append(
                ToolCall(id=call.id, name=call.function.name, arguments=arguments)
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("invalid_provider_tool_call id=%s error=%s", call.id, exc)
    return parsed_calls


def to_provider_tool_message(result: ToolResultMessage) -> dict[str, str]:
    """仅在此适配层映射回 OpenAI/DeepSeek 消息格式。"""

    return {
        "role": "tool",
        "tool_call_id": result.tool_call_id,
        "content": result.content,
    }


async def run_agent(user_prompt: str) -> str:
    """完整闭环：LLM 决策、Runtime 执行、结果写回，并请求下一轮 LLM 回复。"""

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")

    enabled = {"query_weather": True, "query_customer_database": True}
    registry = build_registry()
    snapshot = registry.snapshot(
        {"query_weather": "1.0.0", "query_customer_database": "1.0.0"}, enabled
    )
    runtime = ToolRuntime(enabled, {"weather-service", "customer-database"})
    context = ExecutionContext(
        user_id="demo-user",
        tenant_id="demo-tenant",
        permissions=frozenset({"weather:read", "database:read"}),
    )
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "你是客服助手。需要天气或客户资料时必须调用已提供工具；不得编造工具结果。",
        },
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(4):
        response = await client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=messages,
            tools=snapshot.provider_tools(),
        )
        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))
        calls = parse_tool_calls(assistant_message)
        if not calls:
            return assistant_message.content or "模型没有返回文本回复。"

        for call in calls:
            result = await runtime.invoke(snapshot, call, context)
            messages.append(to_provider_tool_message(result))

    return "工具调用轮数已达上限。"


async def main() -> None:
    prompt = "请查询北京天气，并查询客户 cust-1001 的会员信息，然后简洁总结。"
    print(await run_agent(prompt))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"运行失败: {exc}", file=sys.stderr)
