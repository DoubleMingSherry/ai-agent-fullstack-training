import json
from validationIssue import ValidationIssue

def build_repair_message(
    issues: list[ValidationIssue],
) -> str:
    details = [issue.model_dump() for issue in issues]

    return (
        "上一份输出未通过 AgentDecision 校验。\n"
        f"校验错误：{json.dumps(details, ensure_ascii=False)}\n"
        "请保持原任务含义不变，只修正 JSON 语法、字段、"
        "类型或字段组合。只返回修正后的 JSON。"
    )