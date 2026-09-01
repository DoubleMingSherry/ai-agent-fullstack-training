from tool_governance_v1 import AuditPhase, AuditRecord, AuditSink


def test_denied_call_has_only_decision_record() -> None:
    sink = AuditSink()
    sink.append(
        AuditRecord(
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
    )

    assert sink.by_trace("trace_denied") == (
        AuditRecord(
            trace_id="trace_denied",
            tool_call_id="call_01",
            tool_name="create_refund",
            user_id="user_01",
            tenant_id="tenant_01",
            phase=AuditPhase.DECISION,
            decision="deny",
            code="PERMISSION_DENIED",
            argument_keys=("amount", "order_id"),
        ),
    )


def test_successful_call_has_decision_and_execution_records() -> None:
    sink = AuditSink()
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
    sink.append(decision)
    sink.append(execution)

    assert sink.by_trace("trace_success") == (decision, execution)
