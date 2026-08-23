from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

# 验收模型的参数是否符合要求
class SearchOrdersInput(StrictModel):
    status: Literal["pending", "paid", "shipped", "cancelled"] | None = None
    created_from: date | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def require_filter(self) -> "SearchOrdersInput":
        if self.status is None and self.created_from is None:
            raise ValueError("status 与 created_from 至少提供一个")
        return self


class OrderSummary(StrictModel):
    order_id: str
    status: Literal["pending", "paid", "shipped", "cancelled"]
    created_at: str
    amount_cents: int = Field(ge=0)

# 验收业务函数的返回值
class SearchOrdersOutput(StrictModel):
    orders: list[OrderSummary]
    total: int = Field(ge=0)

# 统一表达错误的方式
class ToolError(StrictModel):
    code: Literal[
        "INVALID_ARGUMENT", # 模型修正参数之后再次调用
        "TOOL_NOT_FOUND",   # 工具不存在
        "PERMISSION_DENIED", # 不能重试统一个调用，应该提示用户切换身份并停止执行
        "APPROVAL_REQUIRED", # 需要用户审批
        "TIMEOUT", # 超时
        "UPSTREAM_ERROR", # 上游错误， 只有满足幂等条件的时候， runtime 才能有限的重试
        "INVALID_OUTPUT", # 业务工具违反的预先定义的返回协议，应记录工程故障，停止执行，不要猜测缺少的字段
    ]
    message: str
    retryable: bool = False