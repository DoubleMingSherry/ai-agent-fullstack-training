"""pip install tokenizers
python 4-1-5-Budget.py                         # 只验证发送前预算
python 4-1-5-Budget.py --send                  # 再真实调用 DeepSeek
python 4-1-5-Budget.py --tokenizer ./tokenizer.json

附件分词器对完整输入 JSON 的分词数是本地估算，不是服务端精确计数。
窗口 1000 是课堂主动设置的容量限制，不是 deepseek-flash 的实际窗口。
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import json
import os
import tempfile
import urllib.request
from tokenizers import Tokenizer


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Budget:
    window: int = 1000
    output: int = 128
    margin: int = 150

    @property
    def input_limit(self):
        limit = self.window - self.output - self.margin
        if self.output <= 0 or self.margin < 0 or limit <= 0:
            raise ValueError("预算配置无效")
        return limit


@dataclass
class Block:
    name: str
    content: str
    priority: int
    required: bool = False
    valid: bool = True
    compact: str | None = None


TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "需要时读取完整日志",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}}, "required": ["path"]}}}]


def make_request(blocks):
    return {"messages": [
        {"role": "system", "content": "依据当前证据分析问题，遵守修改限制。"},
        {"role": "user", "content": "修复登录会话到期后仍然有效的问题。"},
    ] + [{"role": "user", "content": f"{b.name}：{text}"}
         for b, text in blocks], "tools": TOOLS}


def allocation_order(blocks):
    return sorted(range(len(blocks)), key=lambda i: (
        not blocks[i].required, -blocks[i].priority, i))


def pack(blocks, budget, count):
    selected = {}
    if count(make_request([])) > budget.input_limit:
        raise ValueError("固定输入已超限，不能发送")
    for i in allocation_order(blocks):
        block = blocks[i]
        if not block.valid:
            if block.required:
                raise ValueError(f"必须项失效，先刷新：{block.name}")
            print(f"排除 {block.name}：证据已失效")
            continue
        versions = [("完整", block.content)]
        if block.compact is not None:
            versions.append(("压缩", block.compact))
        for label, text in versions:
            candidate = {**selected, i: (block, text)}
            request = make_request([candidate[j] for j in sorted(candidate)])
            used = count(request)
            print(f"尝试 {block.name}/{label}：{used} / {budget.input_limit}")
            if used <= budget.input_limit:
                selected = candidate
                break
        else:
            if block.required:
                raise ValueError(f"必须项 {block.name} 压缩后仍超限，拒绝发送")
            print(f"跳过 {block.name}：剩余预算不足")
    request = make_request([selected[i] for i in sorted(selected)])
    assert count(request) <= budget.input_limit
    return request


def send(request, budget):
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("未读取到 DEEPSEEK_API_KEY；本地预算验证已完成")
    payload = {**request, "model": "deepseek-flash",
               "thinking": {"type": "disabled"}, "max_tokens": budget.output,
               "tool_choice": "none", "stream": True,
               "stream_options": {"include_usage": True}}
    req = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=dump(payload).encode(), headers={"Content-Type": "application/json",
        "Authorization": f"Bearer {key}"}, method="POST")
    usage = None
    with urllib.request.urlopen(req, timeout=60) as response:
        for line in response:
            line = line.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("usage") is not None:
                usage = chunk["usage"]
    if usage is None:
        raise RuntimeError("服务端未返回 usage")
    return usage


def main(args, workdir):
    tokenizer = Tokenizer.from_file(args.tokenizer)
    # 覆盖 messages、tools 和 JSON 包装；模型内部模板仍可能不同。
    def count(request):
        return len(tokenizer.encode(dump(request), add_special_tokens=False).ids)

    budget = Budget()
    failure = "test_at_expiry：now=100，expires_at=100；预期 False，实际 True。"
    log = "启动信息和依赖检查通过\n" * 2000 + failure
    path = workdir / "session_tests.log"
    path.write_text(log, encoding="utf-8")
    blocks = [
        Block("业务规则和限制", "到期立即失效；不得修改现有测试。", 100, True),
        Block("当前源码", "return now <= expires_at", 90, True),
        Block("当前失败", log, 100, True,
              compact=failure + f"\n完整日志：{path}"),
        Block("当前状态", "失败已复现，补丁未应用。", 80),
        Block("支付文档", "无关支付模块说明\n" * 1000, 10),
        Block("旧测试", "旧版本全部通过。", 95, valid=False),
    ]
    print(f"课堂容量 {budget.window} - 输出 {budget.output} - 余量 {budget.margin}"
          f" = 输入预算 {budget.input_limit}")
    raw = make_request([(b, b.content) for b in blocks if b.valid])
    print("全部装入的本地估算：", count(raw))
    request = pack(blocks, budget, count)
    estimated = count(request)
    print("装配后的本地估算：", estimated)
    print("最终请求：", json.dumps(request, ensure_ascii=False, indent=2))
    print("\n反例：把输入预算缩到 1，检查是否拒绝发送")
    try:
        pack(blocks, Budget(window=279, output=128, margin=150), count)
    except ValueError as error:
        print("已拦截：", error)
    else:
        raise AssertionError("应拒绝超限请求")
    print("\n反例：必须项压缩后仍然过长")
    oversized = Block("必要证据", log, 100, True, compact=log[:len(log) // 2])
    try:
        pack([oversized], budget, count)
    except ValueError as error:
        print("已拦截：", error)
    else:
        raise AssertionError("不应删除超限的必须项后继续发送")
    if args.send:
        usage = send(request, budget)
        print("DeepSeek 实际 usage：", dump(usage))
        print("实际输入与估算之差：", usage["prompt_tokens"] - estimated)
        print("实际输入是否在课堂预算内：", usage["prompt_tokens"] <= budget.input_limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", default=str(Path(__file__).with_name("tokenizer.json")))
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="budget_demo_") as directory:
            main(args, Path(directory))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"演示未完成：{error}\n")
