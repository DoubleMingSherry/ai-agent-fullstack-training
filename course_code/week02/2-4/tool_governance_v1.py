from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import json
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict


class ToolArguments(BaseModel):
    # Schema 层：模型生成的工具参数边界，拒绝未在 Schema 中声明的字段。
    model_config = ConfigDict(extra="forbid")


class Effect(StrEnum):
    # 工具的副作用类别，供运行时按读、写或命令执行施加不同治理策略。
    READ = "read"
    WRITE = "write"
    SHELL = "shell"


class RiskLevel(StrEnum):
    # 工具风险等级，供权限、审批和审计规则使用。
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValidationLayer(StrEnum):
    # 四层校验分别回答“参数合法、业务可做、主体有权、当前动作已确认”。
    SCHEMA = "schema"
    BUSINESS_PRECHECK = "business_precheck"
    PERMISSION = "permission"
    APPROVAL = "approval"


@dataclass(frozen=True, slots=True)
class ValidationLayerGuide:
    # 课程用的校验职责映射；它描述数据归属，不替代运行时门禁优先级。
    layer: ValidationLayer
    judgment: str
    data_source: str
    failure_examples: tuple[str, ...]


VALIDATION_LAYER_GUIDE: tuple[ValidationLayerGuide, ...] = (
    ValidationLayerGuide(
        layer=ValidationLayer.SCHEMA,
        judgment="字段、类型、格式和范围是否符合参数契约",
        data_source="ToolDefinition.parameters_model",
        failure_examples=("金额为负", "包含未知字段", "订单号格式错误"),
    ),
    ValidationLayerGuide(
        layer=ValidationLayer.BUSINESS_PRECHECK,
        judgment="资源归属、当前状态、额度和依赖事实是否允许本次操作",
        data_source="Repository / 业务 API",
        failure_examples=("订单不属于当前租户", "订单已退款", "可退金额不足"),
    ),
    ValidationLayerGuide(
        layer=ValidationLayer.PERMISSION,
        judgment="当前主体是否具备申请该工具调用的资格",
        data_source="ExecutionContext / IAM",
        failure_examples=("普通客服没有退款权限",),
    ),
    ValidationLayerGuide(
        layer=ValidationLayer.APPROVAL,
        judgment="用户是否确认当前参数对应的具体动作",
        data_source="审批服务",
        failure_examples=("审批后金额改变", "审批已过期", "审批已使用"),
    ),
)


class RecoveryMechanism(StrEnum):
    # 恢复机制发生在不同层次，不能把它们都当成“重试”。
    PROVIDER_RETRY = "provider_retry"
    AGENT_REPLAN = "agent_replan"
    TOOL_RUNTIME_RETRY = "tool_runtime_retry"
    BUSINESS_STATUS_RECOVERY = "business_status_recovery"


@dataclass(frozen=True, slots=True)
class RecoveryMechanismGuide:
    # 课程用恢复职责映射：先区分是否产生新决策，再决定是否可以重放业务动作。
    mechanism: RecoveryMechanism
    occurs_at: str
    creates_new_model_decision: bool | None
    replays_same_business_action: bool | None
    typical_scenarios: tuple[str, ...]


RECOVERY_MECHANISM_GUIDE: tuple[RecoveryMechanismGuide, ...] = (
    RecoveryMechanismGuide(
        mechanism=RecoveryMechanism.PROVIDER_RETRY,
        occurs_at="调用 LLM API 时",
        creates_new_model_decision=False,
        replays_same_business_action=False,
        typical_scenarios=("429", "连接失败", "模型服务 5xx", "尚未进入 handler"),
    ),
    RecoveryMechanismGuide(
        mechanism=RecoveryMechanism.AGENT_REPLAN,
        occurs_at="Tool Result 回填后的下一轮",
        creates_new_model_decision=True,
        replays_same_business_action=None,
        typical_scenarios=("INVALID_ARGUMENT 后模型修正金额", "参数和工具都可能变化"),
    ),
    RecoveryMechanismGuide(
        mechanism=RecoveryMechanism.TOOL_RUNTIME_RETRY,
        occurs_at="execute 阶段",
        creates_new_model_decision=False,
        replays_same_business_action=True,
        typical_scenarios=("只读查询遇到瞬时数据库断连", "必须先判断幂等性"),
    ),
    RecoveryMechanismGuide(
        mechanism=RecoveryMechanism.BUSINESS_STATUS_RECOVERY,
        occurs_at="结果未知或长流程中断后",
        creates_new_model_decision=None,
        replays_same_business_action=False,
        typical_scenarios=("支付请求超时后查询退款单", "先查状态，不直接重放"),
    ),
)


class FailureKind(StrEnum):
    # 失败类别决定恢复动作；不能只按异常名称盲目重试。
    INVALID_ARGUMENT = "invalid_argument"
    PERMISSION_OR_DENY = "permission_or_deny"
    TRANSIENT_READ_FAILURE = "transient_read_failure"
    IDEMPOTENT_WRITE_FAILURE = "idempotent_write_failure"
    NON_IDEMPOTENT_WRITE_TIMEOUT = "non_idempotent_write_timeout"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


@dataclass(frozen=True, slots=True)
class FailureRecoveryGuide:
    # 课程用失败处理矩阵：明确何时允许恢复，以及必须禁止的危险动作。
    failure: FailureKind
    tool_attributes: str
    recommended_action: str
    prohibited_action: str


FAILURE_RECOVERY_GUIDE: tuple[FailureRecoveryGuide, ...] = (
    FailureRecoveryGuide(
        failure=FailureKind.INVALID_ARGUMENT,
        tool_attributes="任意",
        recommended_action="返回 INVALID_ARGUMENT，允许模型修正参数",
        prohibited_action="使用原参数重试",
    ),
    FailureRecoveryGuide(
        failure=FailureKind.PERMISSION_OR_DENY,
        tool_attributes="任意",
        recommended_action="结束、切换可信主体或转人工处理",
        prohibited_action="让模型改写提示词绕过权限",
    ),
    FailureRecoveryGuide(
        failure=FailureKind.TRANSIENT_READ_FAILURE,
        tool_attributes="read",
        recommended_action="在总 deadline 内指数退避并有限重试",
        prohibited_action="无限重试占满资源",
    ),
    FailureRecoveryGuide(
        failure=FailureKind.IDEMPOTENT_WRITE_FAILURE,
        tool_attributes="write + 真实幂等",
        recommended_action="使用同一幂等键有限重试",
        prohibited_action="每次生成新的幂等键",
    ),
    FailureRecoveryGuide(
        failure=FailureKind.NON_IDEMPOTENT_WRITE_TIMEOUT,
        tool_attributes="write",
        recommended_action="返回 TIMEOUT_UNKNOWN，并查询真实业务状态",
        prohibited_action="直接重放业务动作",
    ),
    FailureRecoveryGuide(
        failure=FailureKind.DEPENDENCY_UNAVAILABLE,
        tool_attributes="任意",
        recommended_action="使用缓存、只读降级或人工兜底",
        prohibited_action="伪造成功结果",
    ),
)


class ResultView(StrEnum):
    # 同一执行事实必须投影为不同视图，避免把内部原始结果直接传播。
    HANDLER_RAW = "handler_raw"
    MODEL = "model"
    USER = "user"
    AUDIT = "audit"


@dataclass(frozen=True, slots=True)
class ResultViewGuide:
    # 课程用结果投影矩阵：不同消费者只能获得完成其职责所需的最小信息。
    view: ResultView
    purpose: str
    may_include: tuple[str, ...]
    must_not_include_by_default: tuple[str, ...]


RESULT_VIEW_GUIDE: tuple[ResultViewGuide, ...] = (
    ResultViewGuide(
        view=ResultView.HANDLER_RAW,
        purpose="内部业务处理",
        may_include=("下游完整对象",),
        must_not_include_by_default=("不直接传播给模型、用户或审计视图",),
    ),
    ResultViewGuide(
        view=ResultView.MODEL,
        purpose="支撑下一步推理",
        may_include=("状态", "可退金额", "业务 ID"),
        must_not_include_by_default=("Token", "密码", "内部堆栈", "无关个人信息"),
    ),
    ResultViewGuide(
        view=ResultView.USER,
        purpose="告知结果与下一步",
        may_include=("用户有权查看的字段",),
        must_not_include_by_default=("内部权限规则", "系统实现细节"),
    ),
    ResultViewGuide(
        view=ResultView.AUDIT,
        purpose="证明决策与执行",
        may_include=("Trace", "Tool Call", "主体", "错误码", "摘要"),
        must_not_include_by_default=("完整 Prompt", "原始客户资料", "密钥"),
    ),
)


class PermissionMode(StrEnum):
    # 权限模式由服务端选择；模型不能通过工具参数切换模式。
    # default：普通交互，低风险动作可返回 confirm。
    # plan：仅分析和读取，执行层始终拒绝写操作和 Shell。
    # bypassPermissions：仅跳过普通低风险确认，不能越过强制审批。
    # dontAsk：无法交互时，将需要确认的动作直接拒绝。
    DEFAULT = "default"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"
    DONT_ASK = "dontAsk"


class PermissionDecision(StrEnum):
    # 鉴权结论区分直接允许、请求确认和不可执行三种状态。
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class PermissionGate(StrEnum):
    # 执行层必须按此顺序评估门禁，后续阶段不能推翻前序拒绝结论。
    DENY = "deny"
    PLAN = "plan"
    ALLOWLIST = "allowlist"
    RBAC = "rbac"
    BUSINESS_PRECHECK = "business_precheck"
    FORCED_APPROVAL = "forced_approval"
    BYPASS = "bypass"
    NORMAL_ALLOW = "normal_allow"


PERMISSION_GATE_PRIORITY: tuple[PermissionGate, ...] = (
    # 硬拒绝 → plan → 白名单 → RBAC → 业务预检查 → 强制审批
    # → bypass → 普通 allow。
    PermissionGate.DENY,
    PermissionGate.PLAN,
    PermissionGate.ALLOWLIST,
    PermissionGate.RBAC,
    PermissionGate.BUSINESS_PRECHECK,
    PermissionGate.FORCED_APPROVAL,
    PermissionGate.BYPASS,
    PermissionGate.NORMAL_ALLOW,
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    # 权限层：服务端可信执行上下文，绝不能由模型通过工具参数自行声明或覆盖。
    trace_id: str
    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    allowed_tools: frozenset[str]
    mode: PermissionMode = PermissionMode.DEFAULT
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    # 策略层：声明副作用、风险、授权前提和审批要求。
    effect: Effect
    risk: RiskLevel
    required_permissions: frozenset[str]
    denied: bool = False
    requires_confirmation: bool = False
    requires_approval: bool = False
    enabled: bool = True


# 处理器、预检和规范化目标都属于运行侧能力，不导出给模型。
ToolHandler: TypeAlias = Callable[
    [ToolArguments, ExecutionContext], Awaitable[Mapping[str, Any]]
]
ToolPrecheck: TypeAlias = Callable[
    # 业务预检查层：通过 Repository 或业务 API 验证资源归属、状态和额度。
    [ToolArguments, ExecutionContext], Awaitable[None]
]
CanonicalTarget: TypeAlias = Callable[[ToolArguments, ExecutionContext], str]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    # 将模型参数契约、运行策略与受控执行能力注册为一个工具定义。
    name: str
    description: str
    parameters_model: type[ToolArguments]
    policy: ToolPolicy
    handler: ToolHandler
    precheck: ToolPrecheck | None
    canonical_target: CanonicalTarget

    def to_model_tool(self) -> dict[str, Any]:
        # 模型侧仅能发现名称、描述和参数 JSON Schema，不能获知治理实现细节。
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_model.model_json_schema(),
            },
        }


class AuditPhase(StrEnum):
    DECISION = "decision"
    EXECUTION = "execution"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    trace_id: str
    tool_call_id: str
    tool_name: str
    user_id: str
    tenant_id: str
    phase: AuditPhase
    decision: str
    code: str | None
    argument_keys: tuple[str, ...]
    latency_ms: float | None = None


class AuditSink:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self._records.append(record)

    def by_trace(self, trace_id: str) -> tuple[AuditRecord, ...]:
        return tuple(record for record in self._records if record.trace_id == trace_id)


def main() -> None:
    sink = AuditSink()
    denied = AuditRecord(
        trace_id="trace_denied",
        tool_call_id="call_01",
        tool_name="create_refund",
        user_id="user_01",
        tenant_id="tenant_01",
        phase=AuditPhase.DECISION,
        decision="deny",
        code="PERMISSION_DENIED",
        argument_keys=("amount", "order_id"),
    )
    decision = AuditRecord(
        trace_id="trace_success",
        tool_call_id="call_02",
        tool_name="get_order",
        user_id="user_01",
        tenant_id="tenant_01",
        phase=AuditPhase.DECISION,
        decision="allow",
        code=None,
        argument_keys=("order_id",),
    )
    execution = AuditRecord(
        trace_id="trace_success",
        tool_call_id="call_02",
        tool_name="get_order",
        user_id="user_01",
        tenant_id="tenant_01",
        phase=AuditPhase.EXECUTION,
        decision="allow",
        code="OK",
        argument_keys=("order_id",),
        latency_ms=12.5,
    )
    for record in (denied, decision, execution):
        sink.append(record)

    for trace_id in ("trace_denied", "trace_success"):
        print(f"{trace_id}:")
        for record in sink.by_trace(trace_id):
            print(json.dumps(asdict(record), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
