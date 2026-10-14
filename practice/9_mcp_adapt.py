from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class ToolDefinition:
    """Host 侧工具定义，包含模型可见 Schema。"""
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.server_id}_{self.name}"

    def to_model_tool(self) -> dict[str, Any]:
        """转换为 OpenAI-compatible 的 function tool 描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.qualified_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
    

FAKE_DIRECTORIES = {
    "orders": [
        {
            "name": "search",
            "description": "按关键词搜索订单",
            "inputSchema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
        {
            "name": "get_status",
            "description": "查询订单状态",
            "inputSchema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    ],
    "github": [
        {
            "name": "search",
            "description": "搜索仓库",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ],
}

def discover(
    directory: dict,
    server_id: str,
) -> dict[str, ToolDefinition]:
    """模拟发现远端 MCP 工具，命名转换：
    以“_”连接server_id和服务接口名，以免不同的服务重名导致无法区分。"""
    tools = {}
    for remote in directory:
        qualified_name = f"{server_id}_{remote['name']}"

        tool = ToolDefinition(
            server_id=server_id,
            name=remote["name"],
            description=remote["description"] or "",
            input_schema=remote["inputSchema"],
        )
        tools[qualified_name] = tool
    return tools

@dataclass(frozen=True)
class CallResult:
    is_error: bool
    message: str
    body: dict

def normalize(
    call_id: str, 
    result: CallResult
) -> dict[str, Any]:
    if result.is_error:
        return {
            "tool_call_id": call_id,
            "ok": False, 
            "code":"MCP_TOOL_ERROR", 
            "message": result.message
        }
    
    serialized_body = json.dumps(result.body, ensure_ascii=False)
    if len(serialized_body) > 2000:
        return {
            "tool_call_id": call_id,
            "ok": False,
            "code": "RESULT_TOO_LARGE",
        }
    
    return {
        "tool_call_id": call_id, 
        "ok": True, 
        "code": "SUCCESS",
        "body": result.body
    }

TRANSPORT = "TRANSPORT"
PROTOCOL = "PROTOCOL"
UNKNOWN = "UNKNOWN"


class ProtocolError(Exception):
    """MCP 协议层错误。"""


def classify(
    exc: Exception
) -> str:
    # 连接异常，返回连接层错误
    if isinstance(exc, ConnectionResetError) or isinstance(exc, ConnectionRefusedError) or isinstance(exc, TimeoutError):
        return TRANSPORT  # 处理位置： Client 连接管理，可重试候选（幂等否）
    # 方法不存在，返回协议层错误
    if isinstance(exc, ProtocolError):
        return PROTOCOL  # 处理位置： 不能重试，返回给模型重新规划
    return UNKNOWN  # 处理位置： 不能重试，返回给模型重新规划

if __name__ == "__main__":

    TOOLS1 = discover(FAKE_DIRECTORIES["orders"], "orders")
    TOOLS2 = discover(FAKE_DIRECTORIES["github"], "github")
    TOOLS = {**TOOLS1, **TOOLS2}
    tool1 = TOOLS["orders_search"]
    assert tool1.server_id == "orders", "server_id错误，应为 orders"
    assert tool1.name == "search", "工具名错误，应为 search"
    assert "订单" in tool1.description, "描述错误，应包含 订单"

    tool2 = TOOLS["github_search"]
    assert tool2.server_id == "github", "server_id错误，应为 github"
    assert tool2.name == "search", "工具名错误，应为 search"
    assert "仓库" in tool2.description, "描述错误，应包含 仓库"

    tool3 = TOOLS["orders_get_status"]
    assert tool3.server_id == "orders", "server_id错误，应为 orders"
    assert tool3.name == "get_status", "工具名错误，应为 get_status"
    assert "订单" in tool3.description, "描述错误，应包含 订单"
    print("Test1 √ discover 返回限定名 ToolDefinition 通过")

    result1 = CallResult(True, "没查询到订单", None)
    normalizeResult1 = normalize("call_1", result1)
    assert normalizeResult1["tool_call_id"] == "call_1", "tool_call_id不一致"
    assert normalizeResult1["ok"] == False
    assert normalizeResult1["code"] == "MCP_TOOL_ERROR"
    print("Test2 √ 工具返回is_error，返回 MCP_TOOL_ERROR 通过")

    data = {
        "id": 1,
        "name": "serialization_demo",
        "description": "这是一个用于演示序列化字符串长度的示例",
        "items": [
            {
                "index": i,
                "value": f"item_value_{i}",
                "note": "A" * 20
            }
            for i in range(80)  # 控制大小，确保超过 2000
        ]
    }
    result2 = CallResult(False, None, data)
    normalizeResult2 = normalize("call_2", result2)
    assert normalizeResult2["tool_call_id"] == "call_2", "tool_call_id不一致"
    assert normalizeResult2["ok"] == False
    assert normalizeResult2["code"] == "RESULT_TOO_LARGE"
    assert "body" not in normalizeResult2, "body不应包含在返回中"
    print("Test3 √ body超长返回 RESULT_TOO_LARGE 通过")

    assert classify(ConnectionResetError()) == TRANSPORT
    print("Test4 √ 返回连接层错误 通过")

    assert classify(ProtocolError()) == PROTOCOL
    print("Test5 √ 返回协议层错误 通过")
    
    assert classify(ValueError()) == UNKNOWN
    print("Test6 √ 返回未知错误 通过")
