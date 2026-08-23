import asyncio

from tool_messages import ToolCall, ToolResultMessage


async def execute_bound(
    call: ToolCall,
    runtime: "ToolRuntime",
    ctx: "ExecutionContext",
) -> tuple[str, ToolResultMessage]:
    result = await runtime.execute(call, ctx)
    return call.id, result


async def execute_batch(
    calls: list[ToolCall],
    runtime: "ToolRuntime",
    ctx: "ExecutionContext",
) -> list[ToolResultMessage]:
    tasks = [
        asyncio.create_task(execute_bound(call, runtime, ctx))
        for call in calls
    ]

    results: list[ToolResultMessage] = []
    for task in asyncio.as_completed(tasks):
        call_id, result = await task
        assert result.tool_call_id == call_id
        results.append(result)

    return results


class DemoRuntime:
    _delays = {
        "call_A": 0.30,
        "call_B": 0.10,
        "call_C": 0.20,
    }

    async def execute(
        self,
        call: ToolCall,
        _ctx: object,
    ) -> ToolResultMessage:
        await asyncio.sleep(self._delays[call.id])
        return ToolResultMessage(
            tool_call_id=call.id,
            name=call.name,
            content=f'{{"order_id":"{call.id}"}}',
        )


async def main() -> None:
    calls = [
        ToolCall(
            id="call_A",
            name="search_orders",
            arguments_json='{"status":"pending"}',
        ),
        ToolCall(
            id="call_B",
            name="search_orders",
            arguments_json='{"status":"paid"}',
        ),
        ToolCall(
            id="call_C",
            name="search_orders",
            arguments_json='{"status":"shipped"}',
        ),
    ]
    results = await execute_batch(calls, DemoRuntime(), ctx=object())

    for result in results:
        print(f"{result.tool_call_id}: {result.content}")


if __name__ == "__main__":
    asyncio.run(main())
