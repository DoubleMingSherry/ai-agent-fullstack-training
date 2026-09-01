@dataclass(frozen=True)
class ToolDefinition:
    # 第一组：调用契约
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    error_codes: tuple[str, ...]

    # 第二组：运行治理
    permission: str
    risk: Literal["low", "medium", "high"]
    timeout_s: float
    retry: RetryPolicy
    audit_fields: tuple[str, ...]
    dependencies: tuple[str, ...]
    execution_mode: Literal["sequential", "parallel"]

    # 第三组：真实实现
    handler: ToolHandler




def to_provider_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_model.model_json_schema(),
        },
    }