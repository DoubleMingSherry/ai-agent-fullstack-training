// packages/ai/src/types.ts
type Tool = {
        name: string;
        description: string;
        parameters: Record<string, unknown>;
};

type ToolCall = {
        id: string;
        name: string;
        arguments: unknown;
};

type ToolResultMessage = {
        role: "toolResult";
        toolCallId: string;
        toolName: string;
        content: string;
};

// packages/agent/src/types.ts
type AgentTool = Tool & {
        execute(
                toolCallId: string,
                args: Record<string, unknown>,
                onUpdate: (content: string) => void,
        ): Promise<string>;
};

type Hooks = {
        beforeToolCall?(call: ToolCall, args: object): Promise<void>;
        afterToolCall?(call: ToolCall, result: string): Promise<string>;
};

// packages/ai/src/utils/validation.ts
function validateToolArguments(call: ToolCall) {
        return call.arguments as Record<string, unknown>;
}

// packages/agent/src/agent-loop.ts
async function prepareToolCall(
        tools: AgentTool[],
        call: ToolCall,
        hooks: Hooks,
) {
        const tool = tools.find(item => item.name === call.name)!;
        const args = validateToolArguments(call);
        await hooks.beforeToolCall?.(call, args);
        return { tool, call, args };
}

async function executePreparedToolCall(
        prepared: Awaited<ReturnType<typeof prepareToolCall>>,
) {
        return prepared.tool.execute(
                prepared.call.id,
                prepared.args,
                content => console.log({
                        type: "tool_execution_update",
                        toolCallId: prepared.call.id,
                        content,
                }),
        );
}

async function finalizeToolCall(
        call: ToolCall,
        result: string,
        hooks: Hooks,
) {
        return await hooks.afterToolCall?.(call, result) ?? result;
}

function createToolResultMessage(
        call: ToolCall,
        content: string,
): ToolResultMessage {
        return {
                role: "toolResult",
                toolCallId: call.id,
                toolName: call.name,
                content,
        };
}

async function runToolCall(
        tools: AgentTool[],
        call: ToolCall,
        hooks: Hooks,
) {
        const prepared = await prepareToolCall(tools, call, hooks);
        const executed = await executePreparedToolCall(prepared);
        const finalized = await finalizeToolCall(call, executed, hooks);
        return createToolResultMessage(call, finalized);
}