from pydantic import BaseModel, Field, ValidationError
from fieldrule import AgentDecision

class ValidationIssue(BaseModel):
    path: str
    code: str
    message: str


class DecisionValidation(BaseModel):
    valid: bool
    decision: AgentDecision | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


def validate_decision(raw_output: str) -> DecisionValidation:
    try:
        decision = AgentDecision.model_validate_json(raw_output)
        return DecisionValidation(
            valid=True,
            decision=decision,
        )
    except ValidationError as exc:
        issues = [
            ValidationIssue(
                path=".".join(str(part) for part in item["loc"]),
                code=item["type"],
                message=item["msg"],
            )
            for item in exc.errors(
                include_url=False,
                include_input=False,
            )
        ]
        return DecisionValidation(
            valid=False,
            issues=issues,
        )