import json

from pydantic import BaseModel

from order_schemas import ToolError
from tool_messages import ToolCall, ToolResultMessage


def success_message(
    call: ToolCall,
    output: BaseModel,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        name=call.name,
        content=output.model_dump_json(),
        is_error=False,
    )
#  OutputSchema  验收业务函数的返回值

def error_message(
    call: ToolCall,
    error: ToolError,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        name=call.name,
        content=json.dumps(
            {"error": error.model_dump()},
            ensure_ascii=False,
        ),
        is_error=True,
    )
