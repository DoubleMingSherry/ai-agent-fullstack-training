

def safe_decide(
    task: str,
    call_model: ModelCall,
    cached: CachedAnswer | None,
) -> DecisionResult:
    try:
        decision = decide_with_repair(
            task=task,
            call_model=call_model,
            max_repairs=2,
        )
        return DecisionResult(
            status="ok",
            source="model",
            decision=decision,
            message=None,
        )
    except StructuredOutputFailure as exc:
        cached_decision = use_cache_if_fresh(cached, task)

        if cached_decision is not None:
            return DecisionResult(
                status="degraded",
                source="cache",
                decision=cached_decision,
                message="模型输出异常，当前结果来自有效缓存。",
                repair_attempts=len(exc.attempts),
            )

        return failed_result(len(exc.attempts))