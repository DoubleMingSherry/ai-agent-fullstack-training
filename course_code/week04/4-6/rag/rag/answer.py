"""上下文装配与带引用的回答提示词（沿用 4-4 的规则）。"""

from __future__ import annotations

PROMPT_TEMPLATE = """你是知识库助手。
规则：
1. 只能依据下面的证据回答，不允许使用证据之外的知识。
2. 每条结论后面用 [编号] 标出它来自哪条证据。
3. 证据里没有写的内容，直接回答“证据里没有写”，不要猜。

证据：
{context}

问题：{question}
"""


def label(hit_or_node) -> str:
    node = getattr(hit_or_node, "node", hit_or_node)
    head = node.metadata.get("heading") or "(正文)"
    return f"{node.metadata.get('file_path', '?')} § {head}"


def assemble(hits, max_chars: int | None = None) -> str:
    """把命中的节点拼成带编号的上下文。"""
    parts = []
    for i, hit in enumerate(hits, 1):
        text = hit.node.text.strip()
        parts.append(f"[{i}] 来源：{label(hit)}\n{text}")
    context = "\n\n".join(parts)
    if max_chars is not None and len(context) > max_chars:
        context = context[:max_chars]
    return context


def build_prompt(question: str, context: str) -> str:
    return PROMPT_TEMPLATE.format(context=context, question=question)
