async def invoke_many(
    runtime: ToolRuntime,
    snapshot: ToolSnapshot,
    calls: list[ToolCall],
    context: ExecutionContext,
) -> list[ToolResultMessage]:
    must_run_in_order = any(
        snapshot.tools.get(call.name)
        and snapshot.tools[call.name].execution_mode == "sequential"
        for call in calls
    )

    if must_run_in_order:
        return [
            await runtime.invoke(snapshot, call, context)
            for call in calls
        ]

    return await asyncio.gather(
        *(runtime.invoke(snapshot, call, context) for call in calls)
    )