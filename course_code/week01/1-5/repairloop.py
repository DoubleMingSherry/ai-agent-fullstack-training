from collections.abc import Callable
from pydantic import BaseModel
from fieldrule import AgentDecision
from validationIssue import ValidationIssue, validate_decision

ModelCall = Callable[
    [list[dict[str, str]], dict[str, object]],
    str,
]


class RepairAttempt(BaseModel):
    attempt: int
    output_preview: str
    issues: list[ValidationIssue]


class StructuredOutputFailure(RuntimeError):
    def __init__(self, attempts: list[RepairAttempt]):
        super().__init__(
            f"structured output failed after {len(attempts)} calls"
        )
        self.attempts = attempts


def decide_with_repair(
    task: str,
    call_model: ModelCall,
    max_repairs: int = 2,
) -> AgentDecision:
    messages = [
        {
            "role": "system",
            "content": "根据 AgentDecision 协议决定搜索或结束。",
        },
        {"role": "user", "content": task},
    ]
    attempts: list[RepairAttempt] = []

    for call_index in range(max_repairs + 1):
        raw_output = call_model(
            messages,
            AgentDecision.model_json_schema(),
        )

        validation = validate_decision(raw_output)
        if validation.valid:
            assert validation.decision is not None
            return validation.decision

        attempts.append(
            RepairAttempt(
                attempt=call_index + 1,
                output_preview=raw_output[:500],
                issues=validation.issues,
            )
        )

        if call_index == max_repairs:
            raise StructuredOutputFailure(attempts)

        messages.extend(
            [
                {"role": "assistant", "content": raw_output[:2000]},
                {
                    "role": "user",
                    "content": build_repair_message(
                        validation.issues
                    ),
                },
            ]
        )

    raise AssertionError("unreachable")