from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel


Permission = Literal["order:read", "order:write"]
RiskLevel = Literal["low", "medium", "high"]
ToolHandler = Callable[
    [BaseModel, "ExecutionContext"],
    Awaitable[BaseModel],
]


@dataclass(frozen=True)
class ToolDefinition:
####### LLM
    name: str
    description: str
    input_model: type[BaseModel]  # model_json_schema()  -> Schema  # 校验参数对不对
####### Runtime
    output_model: type[BaseModel]
    error_model: type[BaseModel]
    permission: Permission
    risk: RiskLevel
    timeout_seconds: float
    max_retries: int
    idempotent: bool
    handler: ToolHandler
##########  公开接口
    def to_model_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }
