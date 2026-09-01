from __future__ import annotations

import sys

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# 如果提示“ModuleNotFoundError: No module named 'mcp'”，请先安装 mcp 库
# pip install mcp

# 然后运行以下代码

mcp = MCPServer(
    "order-service",
    instructions="查询订单必须先调用 get_order，不要猜测订单状态。",
)

ORDERS = {
    "ord_1001": {
        "order_id": "ord_1001",
        "status": "pending",
        "amount_cents": 2999,
    },
    "ord_1002": {
        "order_id": "ord_1002",
        "status": "shipped",
        "amount_cents": 1599,
    },
}

"""用于演示查询与售后工单流程的内存订单数据。"""

@mcp.tool(structured_output=True)
def get_order(order_id: str) -> dict[str, object]:
    """按订单号查询订单状态。找不到订单时返回工具错误。"""
    order = ORDERS.get(order_id)
    if not order:
        raise ToolError("ORDER_NOT_FOUND")
    return {"ok": True, **order}


@mcp.tool(structured_output=True)
def create_ticket(order_id: str, reason: str) -> dict[str, object]:
    """为已存在的订单创建售后工单。"""
    if order_id not in ORDERS:
        raise ToolError("ORDER_NOT_FOUND")
    if not 4 <= len(reason) <= 100:
        raise ToolError("INVALID_REASON")
    return {"ok": True, "ticket_id": "ticket_2001", "order_id": order_id}


@mcp.resource("policy://order-status")
def order_status_policy() -> str:
    """订单状态说明，只提供只读上下文。"""
    return "pending=待处理；shipped=已发货；cancelled=已取消"


@mcp.prompt()
def order_assistant(order_id: str) -> str:
    """生成订单查询任务模板。"""
    return f"查询订单 {order_id}。必须使用工具获取真实状态，不要猜测。"

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run("streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run("stdio")
