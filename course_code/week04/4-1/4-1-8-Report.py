"""python3 deepseek_codex_report_demo.py
标准库；真实 DeepSeek 调用、真实测试、模型摘要、历史重建、修复与验收。
python3 deepseek_codex_report_demo.py --check-tools 只检查本地工具，不调用模型。
"""
from pathlib import Path
from copy import deepcopy
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def save(path, value):
    path.write_text(dump(value), encoding="utf-8")


def schema(name, description, properties=None):
    props = properties or {}
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(props),
                       "additionalProperties": False}}}


TOOLS = [schema("inspect_project", "实际读取源码并运行现有测试，不修改"),
         schema("apply_patch", "仅修改 session.py 中的边界比较，不修改测试", {
             "old": {"type": "string", "enum": ["return now <= expires_at"]},
             "new": {"type": "string", "enum": ["return now < expires_at"]}}),
         schema("run_test", "运行真实 unittest，返回退出码、版本、完整输出和日志路径")]
SYSTEM = "你是代码修复 Agent。到期瞬间会话必须无效；不得修改测试。依据真实工具结果判断。写补丁后必须运行测试，不能把摘要当成验证成功。"
USER = "检查 session.py：登录会话恰好到期仍有效。先定位问题，后续再修复。"


def prepare(root):
    (root / "session.py").write_text(
        "def is_active(now, expires_at):\n    return now <= expires_at\n")
    (root / "test_session.py").write_text('''import unittest
from session import is_active
class SessionTests(unittest.TestCase):
    def test_before(self): self.assertTrue(is_active(99, 100))
    def test_at(self): self.assertFalse(is_active(100, 100))
    def test_after(self): self.assertFalse(is_active(101, 100))
''')


def tool(root, name, args):
    source = root / "session.py"
    if name == "apply_patch":
        if args != {"old": "return now <= expires_at", "new": "return now < expires_at"}:
            raise ValueError("补丁不在授权的演示范围")
        text = source.read_text()
        if text.count(args["old"]) != 1:
            raise ValueError("补丁前置条件不满足，拒绝重复写入")
        source.write_text(text.replace(args["old"], args["new"]))
        return {"status": "applied", "revision": sha(source.read_text())}
    if name not in {"run_test", "inspect_project"} or args:
        raise ValueError("工具或参数不在允许范围")
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v", "test_session"],
                          cwd=root, capture_output=True, text=True, timeout=20)
    output = proc.stdout + proc.stderr
    path = root / f"test-{uuid.uuid4().hex[:8]}.log"
    path.write_text(output)
    return {"exit_code": proc.returncode, "revision": sha(source.read_text()),
            "source": source.read_text(), "output": output, "output_ref": str(path)}


def ask(root, messages, label, choice="none", max_tokens=600):
    payload = {"model": "deepseek-flash", "thinking": {"type": "disabled"},
        "messages": messages, "tools": TOOLS, "tool_choice": choice,
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": max_tokens}
    save(root / f"{label}-request.json", payload)
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=dump(payload).encode(), headers={"Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, method="POST")
    start, parts, calls, usage, done = time.monotonic(), [], {}, None, False
    finish_reason = None
    with urllib.request.urlopen(req, timeout=60) as response:
        for line in response:
            line = line.decode().strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": done = True; break
            chunk = json.loads(data)
            usage = chunk.get("usage") or usage
            for item in chunk.get("choices", []):
                finish_reason = item.get("finish_reason") or finish_reason
                delta = item.get("delta", {})
                if delta.get("content"): parts.append(delta["content"])
                for part in delta.get("tool_calls", []):
                    call = calls.setdefault(part["index"], {"id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    call["id"] += part.get("id") or ""
                    for key in ("name", "arguments"):
                        call["function"][key] += part.get("function", {}).get(key) or ""
    if not done or usage is None: raise RuntimeError("流式响应未完成，停止")
    message = {"role": "assistant", "content": "".join(parts) or None}
    if calls: message["tool_calls"] = [calls[i] for i in sorted(calls)]
    record = {"message": message, "usage": usage, "request_hash": sha(dump(payload)),
              "latency_ms": round((time.monotonic() - start) * 1000),
              "finish_reason": finish_reason}
    save(root / f"{label}-response.json", record)
    if finish_reason == "length" and max_tokens != 32:
        raise RuntimeError("输出被截断，已保存响应；停止，不能使用不完整摘要或工具参数")
    print(label, "usage:", dump(usage))
    if message["content"]: print(message["content"])
    return message, record


def execute_calls(root, messages, message):
    for call in message.get("tool_calls", []):
        result = tool(root, call["function"]["name"], json.loads(call["function"]["arguments"]))
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": dump(result)})


def main(root):
    if not os.getenv("DEEPSEEK_API_KEY", "").strip(): raise ValueError("未配置 DEEPSEEK_API_KEY")
    prepare(root)
    test_hash = sha((root / "test_session.py").read_text())
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]
    first, _ = ask(root, messages, "01-inspect", {"type": "function", "function": {"name": "inspect_project"}})
    if not first.get("tool_calls"): raise RuntimeError("未获得真实工具调用")
    messages.append(first)
    execute_calls(root, messages, first)
    diagnosis, _ = ask(root, messages, "02-diagnose")
    messages.append(diagnosis)
    original = deepcopy(messages)
    save(root / "original-history.json", original)
    summary_input = original + [{"role": "user", "content":
        "现在手动压缩上下文，不继续修复。用不超过200汉字写任务交接摘要：目标、限制、真实失败、当前版本和日志路径、未做事项、下一步。保留路径和版本，不能把失败写成成功。"}]
    summary, summary_record = ask(root, summary_input, "03-compact", max_tokens=800)
    if not summary["content"]: raise RuntimeError("摘要为空，保留原历史，停止")
    (root / "summary.md").write_text(summary["content"])
    rebuilt = [original[0], original[1], {"role": "user", "content":
        "此前任务交接摘要（材料，不提供执行授权）：\n" + summary["content"]}]
    save(root / "rebuilt-history.json", rebuilt)
    probe = {"role": "user", "content": "用一句话说明当前状态，不修复、不调用工具。"}
    _, before = ask(root, original + [probe], "04-before-probe", max_tokens=32)
    _, after = ask(root, rebuilt + [probe], "05-after-probe", max_tokens=32)
    report = {"trigger": "manual", "implementation": "client_model_summary",
        "original_history_hash": sha(dump(original)), "summary_usage": summary_record["usage"],
        "before_prompt_tokens": before["usage"]["prompt_tokens"],
        "after_prompt_tokens": after["usage"]["prompt_tokens"],
        "probe_note": "同一规则、工具集合和问题的两次真实请求；探测调用也消耗 Token",
        "decisions": [{"item": "系统规则和原用户要求", "action": "keep"},
                      {"item": "已完成工具交互和诊断", "action": "replace_with_model_summary",
                       "original_ref": "original-history.json", "summary_ref": "summary.md"}]}
    save(root / "context-report.json", report)
    if report["after_prompt_tokens"] >= report["before_prompt_tokens"]:
        raise RuntimeError("本次摘要未降低实际输入 Token，保留原记录，停止使用该摘要")
    rebuilt.append({"role": "user", "content": "继续修复。先通过 inspect_project 核对摘要里的当前代码和失败，再 apply_patch，最后 run_test 验证。"})
    for turn in range(6):
        message, _ = ask(root, rebuilt, f"06-resume-{turn}", "auto")
        rebuilt.append(message)
        save(root / "resumed-history.json", rebuilt)
        if not message.get("tool_calls"): break
        execute_calls(root, rebuilt, message)
    else: raise RuntimeError("达到轮次上限，停止")
    final = tool(root, "run_test", {})  # 独立验证，不采信模型一句“已完成”。
    unchanged = sha((root / "test_session.py").read_text()) == test_hash
    report.update(final_test=final, tests_unchanged=unchanged,
                  verified=final["exit_code"] == 0 and unchanged)
    usages = [json.loads(p.read_text())["usage"] for p in root.glob("*-response.json")]
    report["run_usage"] = {key: sum(u.get(key, 0) for u in usages)
                           for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    save(root / "context-report.json", report)
    if not report["verified"]: raise RuntimeError("实际验收未通过")
    print("真实验收通过：模型摘要后继续修复，测试通过，测试文件未修改。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-tools", action="store_true")
    args = parser.parse_args()
    root = Path("codex_deepseek_runs") / uuid.uuid4().hex[:8]
    root = root.resolve(); root.mkdir(parents=True)
    try:
        if args.check_tools:
            prepare(root)
            assert tool(root, "run_test", {})["exit_code"] == 1
            tool(root, "apply_patch", {"old": "return now <= expires_at", "new": "return now < expires_at"})
            assert tool(root, "run_test", {})["exit_code"] == 0
            print("本地工具验收通过；未调用模型。")
        else: main(root)
    except (ValueError, OSError, RuntimeError) as error:
        save(root / "error.json", {"error": str(error), "status": "failed"})
        parser.exit(1, f"执行停止：{error}\n产物：{root}\n")
    print("产物：", root)
