import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

from mcp_client_v1 import base_dir, result_payload


MAX_ROUNDS = 4
SYSTEM_PROMPT = (
    "你是订单助手。查询订单时必须调用 get_order，不能编造结果。"
    "如果订单不存在，可以根据用户目标调整计划；不能把同一个失败调用盲目重试。"
)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ProviderReply:
    text: str | None
    tool_calls: list[ToolCall]
    message: dict[str, Any]


class OpenAIProvider:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        """保存 OpenAI-compatible 异步客户端和目标模型名称。"""
        self.client = client
        self.model = model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model_tools: list[dict[str, Any]],
    ) -> ProviderReply:
        """请求模型，并把响应转换为 Loop 使用的统一回复格式。"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=model_tools,
            tool_choice="auto",
        )
        assistant = response.choices[0].message
        tool_calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments_json=call.function.arguments,
            )
            for call in assistant.tool_calls or []
        ]
        return ProviderReply(
            text=assistant.content if not tool_calls else None,
            tool_calls=tool_calls,
            message=assistant.model_dump(exclude_none=True),
        )


class MCPToolRuntime:
    def __init__(self, client: Client, schemas: dict[str, dict[str, Any]]) -> None:
        """保存 MCP 客户端、工具参数 Schema 和执行轨迹。"""
        self.client = client
        self.schemas = schemas
        self.trace: list[dict[str, Any]] = []

    async def execute(self, call: ToolCall) -> dict[str, Any]:
        """先校验模型参数，再调用远端 MCP 工具并记录执行事实。"""
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            return self._reject(call, "INVALID_ARGUMENT", "工具参数不是合法 JSON。")

        error = validate_arguments(self.schemas.get(call.name), arguments)
        if error:
            return self._reject(call, "INVALID_ARGUMENT", error)

        self.trace.append(
            {
                "event": "server_call",
                "tool_call_id": call.id,
                "name": call.name,
                "arguments": arguments,
            }
        )
        try:
            result = await self.client.call_tool(call.name, arguments)
        except Exception as exc:
            payload = {"ok": False, "code": "MCP_TRANSPORT_ERROR", "message": str(exc)}
        else:
            payload = result_payload(result)

        self.trace.append(
            {
                "event": "tool_result",
                "tool_call_id": call.id,
                "name": call.name,
                "payload": payload,
            }
        )
        return payload

    def _reject(self, call: ToolCall, code: str, message: str) -> dict[str, Any]:
        """记录本地拒绝事件，并返回不会触发远端调用的错误结果。"""
        payload = {"ok": False, "code": code, "message": message}
        self.trace.append(
            {
                "event": "runtime_rejected",
                "tool_call_id": call.id,
                "name": call.name,
                "payload": payload,
            }
        )
        return payload


def validate_arguments(schema: dict[str, Any] | None, value: Any) -> str | None:
    """按 MCP 工具 Schema 校验参数，返回错误说明或 None。"""
    if schema is None:
        return "工具未注册。"
    if not isinstance(value, dict):
        return "工具参数必须是 JSON 对象。"

    properties = schema.get("properties", {})
    missing = [name for name in schema.get("required", []) if name not in value]
    if missing:
        return f"缺少必填参数: {', '.join(missing)}。"

    if schema.get("additionalProperties") is False:
        unexpected = set(value) - set(properties)
        if unexpected:
            return f"包含未定义参数: {', '.join(sorted(unexpected))}。"

    for name, item in properties.items():
        if name not in value:
            continue
        expected_type = item.get("type")
        if expected_type == "string" and not isinstance(value[name], str):
            return f"参数 {name} 必须是字符串。"
        if expected_type == "integer" and (
            not isinstance(value[name], int) or isinstance(value[name], bool)
        ):
            return f"参数 {name} 必须是整数。"
    return None


def to_provider_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """把 MCP 工具描述转换为 OpenAI-compatible function tools。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in mcp_tools
    ]


async def run_order_agent(
    user_text: str,
    provider: OpenAIProvider,
    runtime: MCPToolRuntime,
    model_tools: list[dict[str, Any]],
    *,
    max_rounds: int = MAX_ROUNDS,
) -> str:
    """执行模型决策、工具调用和结果回写，直到得到最终文本或到达上限。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    for round_no in range(1, max_rounds + 1):
        reply = await provider.complete(messages, model_tools)
        messages.append(reply.message)

        if reply.text is not None:
            return reply.text
        if not reply.tool_calls:
            return "模型没有返回文本或工具调用。"

        for tool_call in reply.tool_calls:
            tool_result = await runtime.execute(tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )
            runtime.trace.append(
                {
                    "event": "result_written_to_context",
                    "round": round_no,
                    "tool_call_id": tool_call.id,
                }
            )

    raise RuntimeError("MAX_ROUNDS_EXCEEDED")


def create_provider() -> OpenAIProvider:
    """读取环境变量并创建不自动重试的 DeepSeek Provider。"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")
    return OpenAIProvider(
        AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            max_retries=0,
        ),
        os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    )


async def main() -> None:
    """启动本地 MCP 服务，发现工具并运行订单查询 Agent。"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(base_dir / "mcp_server_v1.py")],
        cwd=base_dir,
    )
    async with Client(stdio_client(parameters)) as client:
        listed_tools = await client.list_tools()
        runtime = MCPToolRuntime(
            client,
            {tool.name: tool.input_schema for tool in listed_tools.tools},
        )
        answer = await run_order_agent(
            "请查询订单 ord_1001 的当前状态，并简洁说明。",
            create_provider(),
            runtime,
            to_provider_tools(listed_tools.tools),
        )
    print(answer)
    print(json.dumps(runtime.trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"运行失败: {exc}", file=sys.stderr)
