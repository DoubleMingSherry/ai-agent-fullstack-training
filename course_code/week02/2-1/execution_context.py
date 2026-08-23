from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    tenant_id: str
    permissions: frozenset[str]
    approved_call_ids: frozenset[str] = field(default_factory=frozenset)
    trace_id: str = ""
    order_service: object | None = None