from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    arguments_json: str


class ToolResultMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False

    def to_model_message(self) -> dict[str, str]:
        return {
            "role": self.role,
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }

#（用户消息、注册工具）模型决策-》（前）工具执行-》（后）观察-》模型再次决策-》...