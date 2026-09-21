"""8_tool_loop_v2.py 
1. 新增策略类（老师 2-2 的简版）：
    @dataclass(frozen=True)
    class RetryPolicy:
        max_attempts: int
        idempotent: bool
        retryable_codes: frozenset
2. ToolDefinition 加字段 retry: RetryPolicy。
   GET_ORDER：幂等、max_attempts=2、retryable_codes={UPSTREAM_ERROR}；
   CREATE_REFUND：非幂等、max_attempts=1。
3. execute 的 handler 调用段改成 attempts 循环：
   can_retry = error.retryable and tool.retry.idempotent 
   and (error.code in tool.retry.retryable_codes) 
   and attempt < max_attempts；
   退避 sleep_fn(0.25 * 2 ** (attempt-1))，
   sleep_fn 作为参数注入（测试传 no-op —— 你 gateway v2 的 backoff_base=0 注入同款）。
4. 三发测试：
   (a) 幂等工具handler抛一次UPSTREAM_ERROR再成功 → 最终success且handler_calls==2；
   (b) 非幂等工具同样失败 → error 收口且 handler_calls==1；
   (c) retryable_codes 白名单外的错误码，即使幂等也不重试。
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
import sys
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import ValidationError
from openai import OpenAI

# Windows GBK 控制台无法编码 ✓/✗ 等字符：强制 UTF-8 输出（Python 3.7+）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    idempotent: bool
    retryable_codes: frozenset

@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    permissions: frozenset
    approved_actions: frozenset
    order_service: DemoOrderService | None = None

# 验收模型的参数是否符合要求
class GetOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    order_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]{3,20}$")


class OrderSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    order_id: str
    status: Literal["pending", "paid", "shipped", "cancelled"]
    created_at: str
    amount_cents: int


class CreateRefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    order_id: str = Field(..., pattern=r"^[a-zA-Z0-9_]{3,20}$")
    amount_cents: int | None
    reason: str | None


# HANDLER_CALLS 全局计数。用于断言副作用：非法调用不执行 handler
HANDLER_CALLS = 0

#================== ToolDefinition start =========================
@dataclass(frozen=True)
class ToolDefinition:
####### LLM
    name: str
    description: str
    input_model: type # model_json_schema()  -> Schema  # 校验参数对不对
####### Runtime
    permission: Permission
    risk: RiskLevel
    handler: ToolHandler
    retry: RetryPolicy
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

def get_order(
    args: GetOrderInput,
    ctx: ExecutionContext,
) -> dict:
    row = ctx.order_service.search(
        order_id=args.order_id,
        user_id=ctx.user_id
    )
    return row

def create_refund(
    args: CreateRefundInput,
    ctx: ExecutionContext,
) -> bool:
    success = ctx.order_service.refund(
        order_id=args.order_id,
        amount_cents=args.amount_cents,
        reason=args.reason,
        user_id=ctx.user_id
    )
    return success

GET_ORDER = ToolDefinition(
    name="get_order",
    description=(
        "根据订单 ID 获取订单详情。"
        "只读取订单；不能取消、退款或修改订单。"
    ),
    input_model=GetOrderInput,
    permission="order:read",
    risk="low",
    handler=get_order,
   # 重试策略：幂等、max_attempts=2、retryable_codes={UPSTREAM_ERROR}
    retry=RetryPolicy(
        max_attempts=2,
        idempotent=True,
        retryable_codes=frozenset({"UPSTREAM_ERROR"}),
    )
)

CREATE_REFUND = ToolDefinition(
    name="create_refund",
    description=(
        "为指定订单创建退款。"
        "只能为已支付的订单申请退款。"
    ),
    input_model=CreateRefundInput,
    permission="order:write",
    risk="high",
    handler=create_refund,
    # 重试策略：非幂等、max_attempts=1
    retry=RetryPolicy(
        max_attempts=1,
        idempotent=False,
        retryable_codes=frozenset(), 
        )   
)

IDEMPOTENT_CREATE_REFUND = ToolDefinition(
    name="idempotent_refund",
    description=(
            "为指定订单创建退款。"
            "只能为已支付的订单申请退款。"
        ),
    input_model=CreateRefundInput,
    permission="order:write",
    risk="high",
    handler=create_refund,
   # 重试策略：幂等、max_attempts=2、retryable_codes={}
    retry=RetryPolicy(
        max_attempts=2,
        idempotent=True,
        retryable_codes=frozenset(),
    )
)
#=================== ToolDefinition end ========================

#=================== DemoOrderService start =========================
class DemoOrderService:

    def __init__(self):
        self._records = [
            {
                "user_id": "user_demo",
                "order_id": "ord_1001",
                "status": "pending",
                "created_at": "2026-08-18T10:30:00+08:00",
                "amount_cents": 2999,
            },
            {
                "user_id": "user_other",
                "order_id": "ord_1002",
                "status": "paid",
                "created_at": "2026-08-18T11:00:00+08:00",
                "amount_cents": 1599,
            },
            {
                "user_id": "user_demo",
                "order_id": "ord_1003",
                "status": "paid",
                "created_at": "2026-08-18T12:00:00+08:00",
                "amount_cents": 3399,
            },
        ]
    def search(
        self,
        order_id: str,
        user_id: str
    ) -> dict:
        print(
            "[业务查询] "
            f"order_id={order_id}"
        )
        record = next((
            record
            for record in self._records
            if record["order_id"] == order_id
        ), None)
        if not record or record["user_id"] != user_id:
            raise ValueError("订单不存在")
        return record
        
    def refund(
            self,
            order_id: str,
            amount_cents: int | None,
            reason: str | None,
            user_id: str 
        ) -> bool:
            record = next((
                        record
                        for record in self._records
                        if record["order_id"] == order_id
                    ), None)
            if not record or record["user_id"] != user_id:
                raise ValueError("订单不存在")
            if record["status"] != "paid":
                raise ValueError("只能为已支付的订单申请退款")
            if amount_cents is not None and amount_cents > record["amount_cents"]:
                raise ValueError("退款金额不能超过订单金额")
            if reason is not None and len(reason) > 200:
                raise ValueError("退款原因不能超过 200 个字符")
            print(
                "[订单退款] "
                f"order_id={order_id}, amount_cents={amount_cents}, reason={reason}"
            )
            global HANDLER_CALLS
            HANDLER_CALLS += 1
            return True

    
#=================== DemoOrderService end ========================

# 统一表达错误的方式
class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal[
        "INVALID_ARGUMENT", # 模型修正参数之后再次调用
        "TOOL_NOT_FOUND",   # 工具不存在
        "PERMISSION_DENIED", # 没有调用该工具的权限
        "APPROVAL_REQUIRED", # 需要用户审批
        "BUSINESS_ERROR",    # 业务工具执行失败（如订单不存在、退款金额超限等）
        "INVALID_OUTPUT", # 业务工具违反的预先定义的返回协议
        "UPSTREAM_ERROR", # 业务工具执行失败
    ]
    message: str
    retryable: bool = False


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    name: str
    arguments_json: str


def run_side_effect_suite() -> None:
    global HANDLER_CALLS
    HANDLER_CALLS = 0
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    runtime.register(CREATE_REFUND)
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),   # 对订单仅有只读权限
        approved_actions=frozenset(),            # 没有任何已批准的高风险动作
        order_service=DemoOrderService()
    )
    toolCalls = [
        # 攻击 1（示范）：模型被诱导在 arguments 里自称已批准、自称 admin
        ToolCall(id="call_id_1", name="create_refund", arguments_json='{"order_id": "ord_1001","approved": true, "role": "admin"}'),
        # 3b: 自己设计第二个变体攻击（换一种伪装方式——比如换个动作名、
        #   把权限塞进 arguments、或伪装成低风险），它也必须被 authorize 拒绝
        ToolCall(id="call_id_2", name="execute_sql", arguments_json='{"order_id": "ord_1001", "query": "...", "risk": "low"}'),
        ToolCall(id="call_id_3", name="create_refund", arguments_json='{"order_id": "ord_1002", "amount_cents": 100, "reason": "customer_request"}'),
    ]
    for call in toolCalls:
        runtime.execute(call, ctx)

    perm_ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read", "order:write"}),   # 对订单有读写权限
        approved_actions=frozenset(),            # 没有任何已批准的高风险动作
        order_service=DemoOrderService()
    )
    runtime.execute(ToolCall(id="call_id_4", name="create_refund", arguments_json='{"order_id": "ord_1002", "amount_cents": 100, "reason": "customer_request"}'), perm_ctx)

    assert HANDLER_CALLS == 0, f"副作用断言失败：非法动作执行了 {HANDLER_CALLS} 次"
    print(f"  ✓ 副作用断言通过：{len(toolCalls)} 个攻击输入，非法工具调用 0 次")

    legit_ctx = ExecutionContext(
        user_id="user_demo", 
        permissions=frozenset({"order:read", "order:write"}), # 对订单有读写权限
        approved_actions=frozenset({"create_refund"}),  # 已批准 create_refund
        order_service=DemoOrderService())
    result1 = runtime.execute(ToolCall(id="call_id_5", name="get_order", arguments_json='{"order_id": "ord_1001"}'), legit_ctx)
    result2 = runtime.execute(ToolCall(id="call_id_6", name="create_refund", arguments_json='{"order_id": "ord_1003", "amount_cents": 100, "reason": "customer_request"}'), legit_ctx)
    result3 = runtime.execute(ToolCall(id="call_id_7", name="create_refund", arguments_json='{"order_id": "ord_1002", "amount_cents": 100, "reason": "customer_request"}'), legit_ctx)
    assert result1.is_error is False and json.loads(result1.content)["order_id"] == "ord_1001"
    print("  ✓ 正例断言通过：合法动作 1/2 放行")
    assert result2.is_error is False and json.loads(result2.content) is True
    assert HANDLER_CALLS == 1, f"副作用断言失败：合法动作未执行，handler_calls={HANDLER_CALLS}"
    print("  ✓ 正例断言通过：合法动作 2/2 放行")
    assert result3.is_error is True and "订单不存在" in result3.content
    assert HANDLER_CALLS == 1, f"副作用断言失败：执行了非法的退款操作，handler_calls={HANDLER_CALLS}"
    print("  ✓ 负例断言通过：权限齐全退款拒绝（订单不属于当前用户）")

MAX_STEPS = 3

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

      
# 一个轮次 = 模型说的一段话 + 它想发的 0 到 N 个 Tool Call
@dataclass(frozen=True)
class AssistantTurn:
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()

class RealModelAdapter:
    def __init__(self, client: OpenAI, model_name: str):
        self._client = client
        self._model = model_name

    def chat(self, messages: list, tools: list) -> AssistantTurn:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            extra_body={"thinking": {"type": "disabled"}},
        )
        m = response.choices[0].message
        calls = tuple(
            ToolCall(id=c.id, name=c.function.name, arguments_json=c.function.arguments)
            for c in (m.tool_calls or [])
        )
        return AssistantTurn(text=m.content, tool_calls=calls)

class FakeModel:
    def __init__(self, turns):
        self._turns = iter(turns)
        self.received = []                # 观测点:记下每轮收到的完整 messages

    def chat(self, messages, tools):
        self.received.append([dict(m) for m in messages])
        return next(self._turns)

def run_order_agent(user_text, runtime, ctx, model, sleep_fn=sleep) -> str:
    messages = [
        {"role": "system", "content": "你是订单助手，不得编造订单。"},
        {"role": "user", "content": user_text},
    ]
    for step in range(1, MAX_STEPS + 1):
        turn = model.chat(messages, runtime.model_tools(ctx))

        # assistant-first writeback:先回写 assistant 消息(带 tool_calls)
        assistant_msg = {"role": "assistant"}
        if turn.text:
            assistant_msg["content"] = turn.text
        if turn.tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                "function": {"name": c.name, "arguments": c.arguments_json}}
                for c in turn.tool_calls
            ]
        messages.append(assistant_msg)

        if not turn.tool_calls:
            return turn.text or ""

        for call in turn.tool_calls:
            result = runtime.execute(call, ctx, sleep_fn = sleep_fn)
            messages.append(result.to_model_message())

    raise RuntimeError("agent exceeded maximum steps")

def run_real() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  --real 需要 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）环境变量")
        return
    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )

    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    runtime.register(CREATE_REFUND)

    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset({"create_refund"}),
        order_service=DemoOrderService(),
    )
    answer = run_order_agent(
            "查一下我订单号ord_1002的订单,然后帮我全额退款。",
            runtime,
            ctx,
            model=RealModelAdapter(client, "deepseek-v4-flash")
        )
    print("\n=== 最终回答 ===")
    print(answer)

Permission = Literal["order:read", "order:write"]
RiskLevel = Literal["low", "medium", "high"]
ToolHandler = Callable[[BaseModel, ExecutionContext], dict | bool]

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

def success_message(
    call: ToolCall,
    output: dict,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        name=call.name,
        content=json.dumps(output, ensure_ascii=False),
        is_error=False,
    )

class MiniToolRuntime:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
    
    def register(self, tool: ToolDefinition) -> Callable[[], None]:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool
    
        def dispose() -> None:
            self._tools.pop(tool.name, None)
    
        return dispose
    
    def model_tools(self, ctx: ExecutionContext) -> list[dict]:
        return [
            tool.to_model_tool()
            for tool in self._tools.values()
            if tool.permission in ctx.permissions
        ]
    
    def _finish_error(
        self,
        call: ToolCall,
        ctx: ExecutionContext,
        started: float,
        error: ToolError,
    ) -> ToolResultMessage:
        elapsed = perf_counter() - started
        print(
            f"Runtime: id={call.id}, name={call.name}, "
            f"error={error.code}:{error.message}, elapsed={elapsed:.3f}s"
        )    
        return error_message(call, error)

    def _finish_success(
        self,
        call: ToolCall,
        output: dict,
    ) -> ToolResultMessage:
        print(
            f"Runtime: id={call.id}, name={call.name}, "
            f"success, output={output}"
        )
        return success_message(call, output)

    def execute(
        self,
        call: ToolCall,
        ctx: ExecutionContext,
        sleep_fn = sleep,
    ) -> ToolResultMessage:
        started = perf_counter()
        tool = self._tools.get(call.name)
        # validate --> authorize --> execute --> finalize

        # 未知动作默认拒绝；
        if tool is None:
            return self._finish_error(call, ctx, started, ToolError(
                code="TOOL_NOT_FOUND",
                message="工具不存在或当前不可用",
            ))
                
        # 1.validate 校验参数是否符合 Input Schema
        try:
            args = tool.input_model.model_validate_json(
                call.arguments_json
            )
        except ValidationError as exc:
            return self._finish_error(call, ctx, started, ToolError(
                code="INVALID_ARGUMENT",
                message="工具输入参数不符合 Input Schema",
            ))
        
        # 2.authorize 检查权限
        # 权限只看 ctx.permissions，绝不读取 call.arguments 里的任何字段
        #    （user_id / role / approved / permission 全都不可信）；
        if tool.permission not in ctx.permissions:
            return self._finish_error(call, ctx, started, ToolError(
                code="PERMISSION_DENIED",
                message="当前身份没有调用该工具的权限",
            ))
        # 高风险动作还必须出现在 ctx.approved_actions 里
        if tool.risk == "high" and call.name not in ctx.approved_actions:
            return self._finish_error(call, ctx, started, ToolError(
                code="APPROVAL_REQUIRED",
                message="该调用需要用户确认",
            ))
    
        # 3.execute 执行 handler
        retry_policy = tool.retry
        for attempt in range(1, retry_policy.max_attempts + 1):
            try:
                output = tool.handler(args, ctx)
                return self._finish_success(call, output)
            except ValueError as exc:
                error = ToolError(
                    code="BUSINESS_ERROR",
                    message=str(exc),
                )
            except Exception:
                error = ToolError(
                    code="UPSTREAM_ERROR",
                    message="工具执行失败",
                    retryable=True,
                )

            # 检查是否可以重试
            can_retry = error.retryable and retry_policy.idempotent and error.code in retry_policy.retryable_codes and attempt < retry_policy.max_attempts
            if not can_retry:
                return self._finish_error(call, ctx, started, error)
            # 退避
            sleep_time = 0.25 * 2 ** (attempt - 1)
            print(f"Runtime: id={call.id}, name={call.name}, attempt={attempt}, error={error}, retrying after {sleep_time:.2f}s")
            sleep_fn(sleep_time)
            
                  

def test_normal_read_order() -> None:
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset(),
        order_service=DemoOrderService(),
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t1_c1", name="get_order", arguments_json='{"order_id": "ord_1001"}'),
        )),
        AssistantTurn(text="订单 ord_1001 状态 pending。", tool_calls=()),
    ])
    answer = run_order_agent("查一下 ord_1001", runtime, ctx, model)

    assert answer == "订单 ord_1001 状态 pending。"
    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    round2 = model.received[1]
    assert round2[2]["role"] == "assistant" and "tool_calls" in round2[2]
    assert round2[3]["role"] == "tool" and round2[3]["tool_call_id"] == "t1_c1"
    print("  ✓ test1 正常轮:两轮结束、assistant-first 回写、id 配对正确")

def test_beyond_max_steps() -> None:
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset(),
        order_service=DemoOrderService(),
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t3_c1", name="get_order", arguments_json='{"order_id": "ord_1001"}'),
        )),
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t3_c2", name="get_order", arguments_json='{"order_id": "ord_1003"}'),
        )),
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t3_c3", name="get_order", arguments_json='{"order_id": "ord_1002"}'),
        )),
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t3_c4", name="get_order", arguments_json='{"order_id": "ord_1001"}'),
        )),
    ])
    try:
        run_order_agent("查一下 ord_1001、ord_1003、ord_1002、ord_1001", runtime, ctx, model)
    except RuntimeError as exc:
        assert str(exc) == "agent exceeded maximum steps"
    else:
        assert False, "应该抛 MAX_STEPS"
    assert len(model.received)==3, "是harness切的，不是脚本耗尽"
    print("  ✓ test3 超过 MAX_STEPS 抛错")

def test_disorder_tool_call_id() -> None:
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset(),
        order_service=DemoOrderService(),
    )

    result1 = runtime.execute(ToolCall(id="t4_a", name="get_order", arguments_json='{"order_id": "ord_1001"}'),ctx=ctx)
    result2 = runtime.execute(ToolCall(id="t4_b", name="get_order", arguments_json='{"order_id": "ord_1003"}'),ctx=ctx)

    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    assert result2.tool_call_id == "t4_b"
    assert result1.tool_call_id == "t4_a"
    print("  ✓ test4 同轮多调用乱序返回结果配对正确")

#=================== DemoRetryOrderService start =========================
class DemoRetryOrderService(DemoOrderService):
    def __init__(self, fail_first=True):
        super().__init__()
        self.calls = 0
        self.fail_first = fail_first

    def search(self, order_id, user_id):
        self.calls += 1
        if self.fail_first:
            self.fail_first = False # 恢复False，避免第二次进入后还抛异常
            raise RuntimeError("模拟上游错误")
        return super().search(order_id, user_id)

    def refund(self, order_id, amount_cents, reason, user_id):
        self.calls += 1
        if self.fail_first:
            self.fail_first = False # 恢复False，避免第二次进入后还抛异常
            raise RuntimeError("模拟上游错误")
        return super().refund(order_id, amount_cents, reason, user_id)
#=================== DemoRetryOrderService end ========================


def test_idempotent_retry_success() -> None:
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    order_service = DemoRetryOrderService()
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset(),
        order_service=order_service,
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t5_c1", name="get_order", arguments_json='{"order_id": "ord_1001"}'),
        )),
        AssistantTurn(text="订单 ord_1001 状态 pending。", tool_calls=()),
    ])


    sleeps = []
    answer = run_order_agent("查一下 ord_1001", runtime, ctx, model, sleep_fn=lambda s: sleeps.append(s))
    assert answer == "订单 ord_1001 状态 pending。"
    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    round = model.received[1]
    assert round[2]["role"] == "assistant" and "tool_calls" in round[2]
    assert round[3]["role"] == "tool" and round[3]["tool_call_id"] == "t5_c1"
    assert order_service.calls== 2, f"幂等工具重试次数不对，实际={order_service.calls}"
    
    assert sleeps == [0.25]
    assert json.loads(round[3]["content"]).get("error")==None
    assert json.loads(round[3]["content"])["status"]=="pending"
    print("  ✓ test5 幂等工具重试成功断言通过：两轮结束、assistant-first 回写、id 配对正确、幂等工具重试次数正确、退避时间正确")

def test_non_idempotent_retry_failure() -> None:
    runtime = MiniToolRuntime()
    runtime.register(CREATE_REFUND)
    order_service = DemoRetryOrderService()
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read", "order:write"}),
        approved_actions=frozenset({"create_refund"}),
        order_service=order_service,
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t6_c1", name="create_refund", arguments_json='{"order_id": "ord_1003", "amount_cents": 100, "reason": "customer_request"}'),
        )),
        AssistantTurn(text="退款失败，请稍后再试。", tool_calls=()),
    ])
    answer = run_order_agent("为 ord_1003 创建退款", runtime, ctx, model)
    assert answer == "退款失败，请稍后再试。"
    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    round = model.received[1]
    assert round[2]["role"] == "assistant" and "tool_calls" in round[2]
    assert round[3]["role"] == "tool" and round[3]["tool_call_id"] == "t6_c1"
    assert order_service.calls == 1, f"非幂等工具重试次数不对，实际={order_service.calls}"
    assert json.loads(round[3]["content"])["error"]["code"] == "UPSTREAM_ERROR"
    print("  ✓ test6 非幂等工具重试失败断言通过：两轮结束、assistant-first 回写、id 配对正确、非幂等工具未重试 正确")


def test_idempotent_disretryable_codes() -> None:
    runtime = MiniToolRuntime()
    runtime.register(GET_ORDER)
    order_service = DemoRetryOrderService(fail_first=False)
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read"}),
        approved_actions=frozenset(),
        order_service=order_service,
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t7_c1", name="get_order", arguments_json='{"order_id": "ord_9999"}'),
        )),
        AssistantTurn(text="订单 ord_9999 不存在。", tool_calls=()),
    ])

    answer = run_order_agent("查一下 ord_9999", runtime, ctx, model)
    assert answer == "订单 ord_9999 不存在。"
    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    round = model.received[1]
    assert round[2]["role"] == "assistant" and "tool_calls" in round[2]
    assert round[3]["role"] == "tool" and round[3]["tool_call_id"] == "t7_c1"
    assert order_service.calls == 1, f"幂等工具非重试码调用次数不对，实际={order_service.calls}"
    assert json.loads(round[3]["content"])["error"]["code"] == "BUSINESS_ERROR"
    print("  ✓ test7 幂等工具非重试码断言通过：两轮结束、assistant-first 回写、id 配对正确、幂等工具非重试码不重试 正确")


def test_idempotent_retryable_codes_nowhitelist() -> None:
    runtime = MiniToolRuntime()
    runtime.register(IDEMPOTENT_CREATE_REFUND)
    order_service = DemoRetryOrderService()
    ctx = ExecutionContext(
        user_id="user_demo",
        permissions=frozenset({"order:read", "order:write"}),
        approved_actions=frozenset({"idempotent_refund"}),
        order_service=order_service,
    )
    model = FakeModel([
        AssistantTurn(text=None, tool_calls=(
            ToolCall(id="t8_c1", name="idempotent_refund", arguments_json='{"order_id": "ord_1003", "amount_cents": 100, "reason": "customer_request"}'),
        )),
        AssistantTurn(text="订单 ord_1003 异常。", tool_calls=()),
    ])

    answer = run_order_agent("将 ord_1003 退款", runtime, ctx, model)
    assert answer == "订单 ord_1003 异常。"
    # 第二轮收到的 messages:回写顺序必须是 assistant 在前、tool 在后
    round = model.received[1]
    assert round[2]["role"] == "assistant" and "tool_calls" in round[2]
    assert round[3]["role"] == "tool" and round[3]["tool_call_id"] == "t8_c1"
    assert order_service.calls == 1, f"幂等工具重试码非白名单调用次数不对，实际={order_service.calls}"
    assert json.loads(round[3]["content"])["error"]["code"] == "UPSTREAM_ERROR"
    print("  ✓ test8 幂等工具重试码非白名单断言通过：两轮结束、assistant-first 回写、id 配对正确、幂等工具重试码非白名单不重试 正确")


def run_offline_tests() -> None:
    print("== 离线测试 start ==")
    test_normal_read_order()
    test_beyond_max_steps()
    test_disorder_tool_call_id()
    test_idempotent_retry_success()
    test_non_idempotent_retry_failure()
    test_idempotent_disretryable_codes()
    test_idempotent_retryable_codes_nowhitelist()
    print("== 离线测试 end ==")

def main() -> None:
    print("== 8_tool_loop ==")
    run_side_effect_suite()
    run_offline_tests()
    if "--real" in sys.argv:
        print("== 真实模型回归 ==")
        run_real()

if __name__ == "__main__":
    sys.exit(main())
