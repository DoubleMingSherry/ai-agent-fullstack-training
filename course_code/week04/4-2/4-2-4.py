import importlib
import json

module = importlib.import_module("4-2-2")

working_memory = {}

working_memory["scratchpad"] = {
    "plan": ["修改比较条件", "运行边界测试"],
    "observation": "恰好到期时仍返回 True",
    "evidence_id": "test-001",
    "status": "已定位，尚未修改",
    "decision_summary": "拟将 <= 改为 <，效果尚未验证",
    "next_step": "应用补丁",
}

context = module.assemble_context(module.system_prompt, module.user_prompt, working_memory)

if __name__ == "__main__":
    print(json.dumps(context, ensure_ascii=False, indent=2))
