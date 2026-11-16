import json
from pathlib import Path

# 完整日志保存在文件中，不全部发送给模型。
log_path = Path("artifacts/session_tests.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
log_path.write_text(
    "启动完成\n"
    "依赖检查完成\n"
    "test_before_expiry: PASSED\n"
    "test_at_expiry: FAILED\n"
    "输入：now=100, expires_at=100\n"
    "预期：False，实际：True\n"
    "test_after_expiry: PASSED\n",
    encoding="utf-8",
)

# 本轮直接提供：规则、限制、当前代码和失败证据。
context = {
    "业务规则": "当前时间等于或大于到期时间时，会话立即失效。",
    "修改限制": "不得修改现有测试，可以新增回归测试。",
    "当前代码": "return now <= expires_at",
    "失败证据": {
        "用例": "test_at_expiry",
        "输入": {"now": 100, "expires_at": 100},
        "预期": False,
        "实际": True,
    },
    "完整日志路径": str(log_path.resolve()),
}

# 提供读取工具的定义，供模型需要时申请补读。
read_file_tool = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取文件，需要查看完整日志时使用",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
}

request = {
    "messages": [
        {
            "role": "system",
            "content": "根据提供的代码和证据分析问题，遵守修改限制。",
        },
        {
            "role": "user",
            "content": "检查登录会话为什么到期后仍然有效。",
        },
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False),
        },
    ],
    "tools": [read_file_tool],
}

print(json.dumps(request, ensure_ascii=False, indent=2))