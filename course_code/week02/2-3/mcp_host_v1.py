from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from jsonschema import ValidationError, validate
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

from mcp_client_v1 import base_dir, result_payload


@dataclass(frozen=True)
class ToolCall:
    """Host 交给 Runtime 执行的一次已解析工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    """Provider 单轮返回的最终文本或单次工具调用。"""

    text: str | None = None
    tool_call: ToolCall | None = None


class LLMProvider(Protocol):
    """定义 Agent Loop 所需的模型调用抽象。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply: ...


class DeepSeekProvider:
    """通过 OpenAI-compatible API 调用 DeepSeek，并规范化模型回复。"""

    def __init__(self) -> None:
        """读取模型配置并创建禁用 SDK 自动重试的异步客户端。"""
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")
        self.model = os.getenv("LLM_MODEL", "deepseek-chat")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            max_retries=0,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply:
        """请求模型，提取首个 Tool Call 或最终文本。"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        message = response.choices[0].message
        if message.tool_calls:
            call = message.tool_calls[0]
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            return ModelReply(
                tool_call=ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
        return ModelReply(text=message.content or "")


ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDefinition:
    """Host 侧工具定义，包含模型可见 Schema 与远端调用处理器。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_model_tool(self) -> dict[str, Any]:
        """转换为 OpenAI-compatible 的 function tool 描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRuntime:
    """在调用 MCP Server 前执行工具查找、参数校验与执行轨迹记录。"""

    def __init__(self) -> None:
        """初始化空的执行轨迹，供副作用和调用顺序断言使用。"""
        self.trace: list[dict[str, Any]] = []

    async def execute(
        self,
        call: ToolCall,
        tools: dict[str, ToolDefinition],
    ) -> dict[str, Any]:
        """校验工具和参数；仅校验通过后才调用对应远端处理器。"""
        tool = tools.get(call.name)
        if not tool:
            return self._reject(call, "TOOL_NOT_FOUND")
        try:
            validate(instance=call.arguments, schema=tool.input_schema)
        except ValidationError as exc:
            return self._reject(call, "INVALID_ARGUMENT", exc.message)

        self.trace.append(
            {
                "event": "server_call",
                "tool_call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            }
        )
        result = await tool.handler(call.id, call.arguments)
        self.trace.append(
            {
                "event": "tool_result",
                "tool_call_id": call.id,
                "name": call.name,
                "payload": result,
            }
        )
        return result

    def _reject(
        self,
        call: ToolCall,
        code: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        """记录本地拒绝，并返回未触发 Server 调用的结构化错误。"""
        payload = {"ok": False, "code": code}
        if message:
            payload["message"] = message
        self.trace.append(
            {
                "event": "runtime_rejected",
                "tool_call_id": call.id,
                "name": call.name,
                "payload": payload,
            }
        )
        return payload


async def discover_tools(
    client: Client,
    server_id: str,
) -> dict[str, ToolDefinition]:
    """发现远端 MCP 工具，命名转换后注册为 Host 可执行工具。"""
    result = await client.list_tools()
    definitions: dict[str, ToolDefinition] = {}
    for remote in result.tools:
        qualified_name = f"{server_id}_{remote.name}"

        async def handler(
            tool_call_id: str,
            arguments: dict[str, Any],
            remote_name: str = remote.name,
        ) -> dict[str, Any]:
            """调用原始 MCP 工具名，并将远端结果归一化为 Host Payload。"""
            response = await client.call_tool(remote_name, arguments)
            return {"tool_call_id": tool_call_id, **result_payload(response)}

        definitions[qualified_name] = ToolDefinition(
            name=qualified_name,
            description=remote.description or "",
            input_schema=remote.input_schema,
            handler=handler,
        )
    return definitions


async def run_agent(
    provider: LLMProvider,
    runtime: ToolRuntime,
    tools: dict[str, ToolDefinition],
    user_input: str,
    max_rounds: int = 4,
) -> str:
    """持续执行模型决策、Runtime 调用和结果回写，直到模型返回文本。"""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_input}]
    model_tools = [tool.to_model_tool() for tool in tools.values()]

    for round_no in range(1, max_rounds + 1):
        reply = await provider.complete(messages, model_tools)
        if reply.text is not None:
            print("LOOP", round_no, "final")
            return reply.text
        if reply.tool_call is None:
            raise RuntimeError("MODEL_REPLY_INVALID")

        print("LOOP", round_no, "tool_call", reply.tool_call.name)
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": reply.tool_call.id,
                        "type": "function",
                        "function": {
                            "name": reply.tool_call.name,
                            "arguments": json.dumps(
                                reply.tool_call.arguments,
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        )
        tool_result = await runtime.execute(reply.tool_call, tools)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": reply.tool_call.id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )
        runtime.trace.append(
            {
                "event": "result_written_to_context",
                "round": round_no,
                "tool_call_id": reply.tool_call.id,
            }
        )

    raise RuntimeError("MAX_ROUNDS_EXCEEDED")


async def main() -> None:
    """启动本地订单 Server，并执行协议契约检查及可选的真实 Agent Loop。"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(base_dir / "mcp_server_v1.py")],
        cwd=base_dir,
    )

    async with Client(stdio_client(parameters)) as client:
        tools = await discover_tools(client, "orders")
        resources = await client.list_resources()
        prompts = await client.list_prompts()

        print("PROTOCOL", client.protocol_version)
        print("TOOLS", sorted(tools))
        print("RESOURCES", [str(item.uri) for item in resources.resources])
        print("PROMPTS", [item.name for item in prompts.prompts])

        runtime = ToolRuntime()
        success = await runtime.execute(
            ToolCall("call_success", "orders_get_order", {"order_id": "ord_1001"}),
            tools,
        )
        assert success["ok"] is True
        print("CONTRACT SUCCESS", success["status"])

        rejected = await runtime.execute(
            ToolCall("call_reject", "orders_get_order", {}),
            tools,
        )
        assert rejected["code"] == "INVALID_ARGUMENT"
        print("REJECT", rejected["code"])

        failed = await runtime.execute(
            ToolCall(
                "call_fail",
                "orders_get_order",
                {"order_id": "ord_missing"},
            ),
            tools,
        )
        assert failed["code"] == "MCP_TOOL_ERROR"
        print("FAIL", failed["code"])

        if "DEEPSEEK_API_KEY" not in os.environ:
            print("LIVE SKIP：设置 DEEPSEEK_API_KEY 后运行真实 Agent Loop")
            print(json.dumps(runtime.trace, ensure_ascii=False, indent=2))
            return

        answer = await run_agent(
            provider=DeepSeekProvider(),
            runtime=runtime,
            tools=tools,
            user_input=(
                "先查询订单 ord_missing；如果工具明确返回订单不存在，"
                "再查询 ord_1002，并告诉我最终查到的订单状态。"
            ),
        )
        print("LIVE", answer)
        print(json.dumps(runtime.trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
