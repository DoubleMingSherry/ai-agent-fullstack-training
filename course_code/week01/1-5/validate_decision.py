from validationIssue import validate_decision

samples = {
    "invalid_json": '{"action": "finish"',
    "missing_field": '{"action": "finish", "query": null}',
    "invalid_combination": (
        '{"action": "finish", "query": null, "answer": null}'
    ),
    "valid": (
        '{"action": "finish", "query": null, "answer": "需要发票。"}'
    ),
}

for name, raw_output in samples.items():
    result = validate_decision(raw_output)
    print(f'name: {name}, valid: {result.valid}, issues: {result.issues}')