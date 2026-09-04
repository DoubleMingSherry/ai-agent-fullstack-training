"""接口层：网关自有请求/响应协议（Pydantic 入口校验）。

上层 agent 只提交：平台逻辑模型名、模板选择（name+version）、消息、业务 Schema。
无论后端是主模型还是备用模型，上层依赖的都是这一份协议（不变量 3）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 上游只接受标准对话角色（如 DeepSeek 拒绝内部角色），在协议层就收紧
Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str = Field(min_length=1)


class PromptRef(BaseModel):
    """Prompt 模板选择：名称 + 版本 + 渲染变量。"""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    variables: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """POST /chat 与 POST /stream 共用的入口协议。

    ``response_schema`` 为调用方的业务 JSON Schema：
    传入即启用 Structured Output 双层校验。
    """

    model: str = Field(min_length=1, description="平台逻辑模型名（白名单内）")
    prompt: PromptRef
    messages: list[Message] = Field(min_length=1)
    response_schema: dict[str, Any] | None = Field(
        default=None,
        description="业务 JSON Schema；提供时走结构化输出与出口校验",
    )


class StreamRequest(ChatRequest):
    """流式入口协议，与 /chat 同一份请求协议。"""


class UsageOut(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


class PromptUsed(BaseModel):
    """实际使用的 Prompt：名称 + 版本 + hash（行为可回放）。"""

    name: str
    version: str
    hash: str


class LLMResponse(BaseModel):
    """统一成功响应：调用方只消费这一份结构。"""

    call_id: str
    model_used: str = Field(description="实际服务请求的模型（主/备）")
    text: str | None = None
    data: Any | None = Field(default=None, description="结构化输出（已通过本地校验）")
    usage: UsageOut
    prompt: PromptUsed
    attempts: int = Field(description="执行层实际尝试次数（含重试与 fallback）")
    latency_ms: float
