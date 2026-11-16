"""标准库演示：python3 4-1-7-check.py
离线验证修复逻辑：python3 4-1-7-check.py --self-test
真实调用读取 DEEPSEEK_API_KEY，deepseek-flash，流式，关闭 thinking。
故障注入：第一个真实模型工具调用在执行前被取消，不产生工具结果。
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import os
import time
import urllib.error
import urllib.request


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def check(messages):
    pending, seen = set(), set()
    for message in messages:
        if pending and message["role"] != "tool":
            raise ValueError(f"INCOMPLETE_TOOL_BATCH: {sorted(pending)}")
        if message["role"] == "tool":
            cid = message.get("tool_call_id")
            if cid not in pending:
                raise ValueError(f"ORPHAN_TOOL_RESULT: {cid}")
            pending.remove(cid)
        for call in message.get("tool_calls", []):
            cid = call.get("id")
            if message["role"] != "assistant" or not cid or cid in seen:
                raise ValueError(f"INVALID_TOOL_CALL_ID: {cid}")
            seen.add(cid)
            pending.add(cid)
    if pending:
        raise ValueError(f"MISSING_TOOL_RESULT: {sorted(pending)}")


def normalize(messages, cancelled_ids):
    """配对修复；只为已确认取消的调用补 aborted，成功不用补。"""
    messages = deepcopy(messages)
    call_ids = {c["id"] for m in messages for c in m.get("tool_calls", [])}
    clean = []
    for message in messages:
        if message["role"] == "tool" and message.get("tool_call_id") not in call_ids:
            print("移除孤立结果：", message.get("tool_call_id"))
        else:
            clean.append(message)
    fixed, i = [], 0
    while i < len(clean):
        message = clean[i]
        fixed.append(message)
        i += 1
        calls = message.get("tool_calls", [])
        if not calls:
            continue
        outputs = []
        while i < len(clean) and clean[i]["role"] == "tool":
            outputs.append(clean[i])
            i += 1
        present = {m["tool_call_id"] for m in outputs}
        for call in calls:
            cid = call["id"]
            if cid not in present:
                if cid not in cancelled_ids:
                    raise ValueError(f"调用尚未结束或状态未知，不能补结果：{cid}")
                outputs.append({"role": "tool", "tool_call_id": cid,
                    "content": dump({"status": "aborted", "executed": False,
                    "reason": "教学故障注入：工具执行前取消；未取得文件内容"})})
                print("补齐 aborted，保留原 ID：", cid)
        fixed.extend(outputs)
    check(fixed)
    return fixed


TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "读取本演示的 session.py，只读",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "enum": ["session.py"]}}, "required": ["path"]}}}]


def ask(messages, trace, choice="auto"):
    check(messages)  # 实际网络请求之前的门禁。
    payload = {"model": "deepseek-flash", "messages": messages, "tools": TOOLS,
        "tool_choice": choice, "thinking": {"type": "disabled"},
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 512}
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=dump(payload).encode(), headers={"Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, method="POST")
    started, text, calls, usage, finished = time.monotonic(), [], {}, None, False
    with urllib.request.urlopen(req, timeout=60) as response:
        for line in response:
            line = line.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            chunk = json.loads(data)
            usage = chunk.get("usage") or usage
            for item in chunk.get("choices", []):
                delta = item.get("delta", {})
                if delta.get("content"):
                    text.append(delta["content"])
                    print(delta["content"], end="", flush=True)
                for part in delta.get("tool_calls", []):
                    call = calls.setdefault(part["index"], {"id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    call["id"] += part.get("id") or ""
                    for field in ("name", "arguments"):
                        call["function"][field] += part.get("function", {}).get(field) or ""
    if not finished or usage is None:
        raise RuntimeError("流式响应不完整；停止，不使用半条工具调用")
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    trace.append({"input": deepcopy(messages), "output": deepcopy(message), "usage": usage,
                  "latency_ms": round((time.monotonic() - started) * 1000)})
    print("\nusage:", dump(usage))
    return message


def self_test():
    # 仅测消息修复规则；不冒充模型响应，也不发请求。
    call = {"role": "assistant", "tool_calls": [{"id": "test_id"}]}
    try:
        check([call])
    except ValueError as error:
        print("发送前拦截：", error)
    fixed = normalize([call], {"test_id"})
    assert normalize(fixed, {"test_id"}) == fixed
    assert json.loads(fixed[-1]["content"])["executed"] is False
    orphan = {"role": "tool", "tool_call_id": "orphan", "content": "不应保留"}
    assert normalize([orphan], set()) == []
    try:
        normalize([call], set())
    except ValueError:
        pass
    else:
        raise AssertionError("运行中或未知状态不能自动补结果")
    print("离线验收通过：缺失拦截、取消补齐、孤立移除、幂等修复、未知状态停止")


def main():
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("当前终端未配置 DEEPSEEK_API_KEY")
    workdir = Path("recovery_demo_artifacts")
    workdir.mkdir(exist_ok=True)
    (workdir / "session.py").write_text(
        "def is_session_valid(now, expires_at):\n    return now <= expires_at\n", encoding="utf-8")
    trace, messages = [], [{"role": "system", "content":
        "先读文件再分析。规则：到期立即失效。aborted 不表示成功；只读工具取消后可以重新申请读取一次。不要修改文件。"},
        {"role": "user", "content": "读取 session.py，解释登录会话为何恰好到期仍有效，给出修改建议。"}]
    try:
        first = ask(messages, trace, {"type": "function", "function": {"name": "read_file"}})
        messages.append(first)
        ids = {c["id"] for c in first.get("tool_calls", [])}
        if not ids:
            raise RuntimeError("模型没有生成工具调用，不能进行本演示")
        print("\n故障注入：工具执行前取消，尚未回写 tool 结果")
        try:
            check(messages)
        except ValueError as error:
            print("本地拦截（未发第二次请求）：", error)
        trace.append({"event": "tool_cancelled_before_execution", "call_ids": sorted(ids)})
        messages = normalize(messages, ids)
        read_done = False
        for _ in range(3):
            message = ask(messages, trace)
            messages.append(message)
            if not message.get("tool_calls"):
                if not read_done:
                    raise RuntimeError("模型没有重新读取文件，不能把它的回答当作已验证分析")
                print("模型完成本轮分析；请检查它是否依据实际源码给出建议")
                break
            for call in message["tool_calls"]:
                arguments = json.loads(call["function"]["arguments"])
                if call["function"]["name"] != "read_file" or arguments.get("path") != "session.py":
                    raise ValueError("工具或路径不在允许范围")
                content = (workdir / "session.py").read_text(encoding="utf-8")
                read_done = True
                print("真实读取完成，新调用 ID：", call["id"])
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
        else:
            raise RuntimeError("达到演示轮次上限，停止")
    finally:
        (workdir / "trace.json").write_text(dump(trace), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        self_test() if args.self_test else main()
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"演示停止：{error}\n")
