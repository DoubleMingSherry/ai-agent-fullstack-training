import json
from pathlib import Path
from tempfile import TemporaryDirectory

with TemporaryDirectory() as directory:
    session_file = Path(directory) / "session.jsonl"

    def append(entry):
        with session_file.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 1. 保存用户要求。
    append({"type": "message", "message": {
        "role": "user",
        "content": "修复会话到期判断，不得修改测试。",
    }})

    # 2. 保存教学代码预设的工具调用。
    append({"type": "message", "message": {
        "role": "assistant",
        "content": [{
            "type": "toolCall",
            "id": "test-001",
            "name": "run_test",
            "arguments": {},
        }],
    }})

    # 3. 实际计算边界表达式，保存结果并关联调用。
    actual = 100 <= 100
    append({"type": "message", "message": {
        "role": "toolResult",
        "toolCallId": "test-001",
        "toolName": "run_test",
        "content": [{
            "type": "text",
            "text": f"预期 False，实际 {actual}",
        }],
        "isError": False,  # 正常获得结果，被测逻辑不符合预期。
    }})

    # 4. 单独保存工作记忆，不自动加入消息列表。
    append({"type": "custom", "customType": "working_memory", "data": {
        "goal": "到期立即失效",
        "constraints": ["不得修改测试"],
        "code_version": "v1",
        "status": "已获得失败证据，尚未修改",
        "evidence": "test-001",
        "next_step": "定位并修改比较条件",
    }})

    # 5. 模拟恢复：重新读取消息和工作记忆。
    entries = [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]
    messages = [
        entry["message"] for entry in entries
        if entry["type"] == "message"
    ]
    memory = next(
        entry["data"] for entry in reversed(entries)
        if entry["type"] == "custom"
        and entry["customType"] == "working_memory"
    )

    # 6. 装配器把工作记忆加入下一轮输入。
    messages.append({
        "role": "user",
        "content": (
            "当前任务记录：\n"
            + json.dumps(memory, ensure_ascii=False)
            + "\n请继续。"
        ),
    })

    print(json.dumps(messages, ensure_ascii=False, indent=2))