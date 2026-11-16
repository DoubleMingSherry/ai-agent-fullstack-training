import importlib
import json

module = importlib.import_module("4-2-2")

fact = module.working_memory["tool_results"][0]

if __name__ == "__main__":
    print(json.dumps(fact, ensure_ascii=False, indent=2))
