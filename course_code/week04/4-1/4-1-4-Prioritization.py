"""4.1.3 装配 + 4.1.4 优先级。运行：python3 context_packing_priority_demo.py

教学数据，不调用模型。本例用材料块数量演示容量限制，不等于 Token 预算。
"""
from dataclasses import dataclass
from pathlib import Path
import json
import tempfile


@dataclass
class Block:
    name: str
    source: str
    content: str
    priority: int
    required: bool = False
    valid: bool = True
    compact: str | None = None


def allocation_order(blocks):
    return sorted(
        range(len(blocks)),
        key=lambda i: (
            not blocks[i].required,  # 必须项优先
            -blocks[i].priority,     # 数值越大，越先分配
            i,                      # 相同优先级保持原顺序
        ),
    )


def pack(blocks, max_blocks):
    """按优先级选材料，再按原始顺序输出；不能静默丢弃必须项。"""
    if max_blocks < 0:
        raise ValueError("容量不能小于零")
    for block in blocks:
        if block.required and not block.valid:
            raise ValueError(f"必须项已失效，先刷新：{block.name}")
    required_count = sum(b.required and b.valid for b in blocks)
    if required_count > max_blocks:
        raise ValueError("容量不足以容纳必须项，先调整材料或预算")

    selected = []
    for i in allocation_order(blocks):
        if blocks[i].valid and len(selected) < max_blocks:
            selected.append(i)

    print("优先分配顺序：", [blocks[i].name for i in selected])
    # 分配顺序决定先给谁空间；输出顺序保持原始排列。
    selected.sort()
    messages = []
    for i in selected:
        block = blocks[i]
        content = block.compact if block.compact is not None else block.content
        messages.append({
            "role": "user",
            "content": f"材料：{block.name}\n来源：{block.source}\n{content}",
        })

    report = []
    for i, block in enumerate(blocks):
        if not block.valid:
            action = "排除：证据已失效"
        elif i not in selected:
            action = "本轮未选：优先级较低，需要时再读"
        else:
            action = "提供压缩版" if block.compact is not None else "直接提供"
        report.append({"材料": block.name, "处理": action})
    return messages, report


READ_FILE = {"type": "function", "function": {
    "name": "read_file", "description": "需要更多细节时读取完整材料",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"},
    }, "required": ["path"]},
}}


def main(workdir):
    # 完整日志写入外部文件；数据仅用于展示装配，不是实际测试结果。
    log_path = workdir / "session_tests.log"
    failure = "test_at_expiry：now=100，expires_at=100；预期 False，实际 True。"
    full_log = "启动与依赖信息\n" * 2000 + failure
    log_path.write_text(full_log, encoding="utf-8")
    blocks = [
        Block("用户目标和限制", "用户要求",
              "修复到期后仍有效的问题；不得修改现有测试。", 100, True),
        Block("业务规则", "当前需求", "到期时立即失效。", 100, True),
        Block("当前源码", "会话校验函数",
              "return now <= expires_at", 90, True),
        Block("旧版通过记录", "旧版本测试",
              "测试全部通过。", 95, valid=False),
        Block("当前失败", str(log_path), full_log, 100, True,
              compact=failure + f"\n完整日志路径：{log_path}"),
        Block("当前状态", "任务记录",
              "失败已复现，补丁未应用；下一步检查到期边界。", 80),
        Block("早期探索", "搜索记录", "已排除密码校验逻辑。", 30),
    ]

    # 七块材料只选五块：四个必须项 + 一个优先级最高的可选项。
    messages, report = pack(blocks, max_blocks=5)
    request = {
        "messages": [{"role": "system", "content":
                      "遵守用户限制，根据当前代码和证据分析问题。"}] + messages,
        "tools": [READ_FILE],
    }
    print("装配报告：\n" + json.dumps(report, ensure_ascii=False, indent=2))
    print("最终请求：\n" + json.dumps(request, ensure_ascii=False, indent=2))
    assert len(messages) == 5
    assert failure in messages[3]["content"]
    assert full_log not in messages[3]["content"]
    assert "测试全部通过" not in json.dumps(request, ensure_ascii=False)
    assert log_path.exists()


if __name__ == "__main__":
    # 日志在演示期间可读，结束时删除；生产环境需要持久保存。
    with tempfile.TemporaryDirectory(prefix="packing_priority_") as directory:
        main(Path(directory))
