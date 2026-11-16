"""上下文装配、带引用的回答、引用与一致性校验。

第四步会在这上面加 Rerank、上下文压缩和证据筛选；
这里先给出第一步用的最小版本。
"""

from __future__ import annotations

import re

from .checks import check_support

PROMPT_TEMPLATE = """你是 login_demo 项目的助手。
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
    return f"{node.metadata['file_path']} § {head}"


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


def verify(answer_text: str, hits) -> dict:
    """校验引用编号，并检查回答里的关键实体是否能在被引证据里找到。"""
    citations = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer_text)})
    valid = [c for c in citations if 1 <= c <= len(hits)]
    bad = [c for c in citations if c not in valid]

    cited_text = "\n".join(hits[c - 1].node.text for c in valid)
    supported, unsupported = check_support(answer_text, cited_text)

    # 有编号但编号越界；或回答里根本没有编号，都算引用不合格。
    ok = bool(valid) and not bad
    if not citations:
        verdict = "无引用"
    elif bad:
        verdict = "引用越界"
    elif not supported and not unsupported:
        verdict = "无可核对实体"
    elif unsupported:
        verdict = "存在无依据实体"
    else:
        verdict = "通过"

    return {
        "citations": citations,
        "bad_citations": bad,
        "cited_sources": {c: label(hits[c - 1]) for c in valid},
        "supported": supported,
        "unsupported": unsupported,
        "citation_ok": ok,
        "verdict": verdict,
    }
