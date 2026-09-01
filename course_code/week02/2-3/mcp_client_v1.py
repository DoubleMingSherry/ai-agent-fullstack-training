import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


base_dir = Path(__file__).parent


def result_payload(result: Any, max_chars: int = 2_000) -> dict[str, Any]:
    """将不可信的 MCP Tool Result 归一化，并限制返回内容大小。"""
    if result.is_error:
        text = getattr(result.content[0], "text", "MCP_TOOL_ERROR")
        return {"ok": False, "code": "MCP_TOOL_ERROR", "message": text[:max_chars]}

    value = result.structured_content
    if value is None:
        value = {"text": getattr(result.content[0], "text", "")}

    if len(json.dumps(value, ensure_ascii=False)) > max_chars:
        return {"ok": False, "code": "RESULT_TOO_LARGE"}
    return dict(value)


async def main() -> None:
    """以 stdio 启动本地 Server，发现工具并演示一次订单查询。"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(base_dir / "mcp_server_v1.py")],
        cwd=base_dir,
    )

    async with Client(stdio_client(parameters)) as client:
        tools = await client.list_tools()
        print("可用工具:", [tool.name for tool in tools.tools])

        result = await client.call_tool("get_order", {"order_id": "ord_1001"})
        print(result_payload(result))


if __name__ == "__main__":
    asyncio.run(main())
