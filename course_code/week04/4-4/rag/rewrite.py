"""查询侧改造：重写、多路改写、分解、澄清。

四种改造都只动问题，不动知识库。
"""

from __future__ import annotations

from .minimax import ask

REWRITE_PROMPT = """把下面的用户问题改写成一句更适合检索的查询。

要求：
- 保留原意，不要回答问题。
- 补上文档里可能用的正式说法和英文/符号名（例如“登出”补成“退出登录 logout”）。
- 去掉“我想知道”“帮我看看”这类口语成分。
- 只输出改写后的那一句，不要解释。

用户问题：{q}
"""

MULTI_PROMPT = """把下面的用户问题改写成 {n} 个不同角度的检索查询，用来并行召回。

要求：
- 每个查询一行，不要编号，不要解释。
- 角度要分开：一个偏正式术语，一个偏代码符号，一个偏可能的文档标题用词。
- 不要回答问题。

用户问题：{q}
"""

DECOMPOSE_PROMPT = """把下面的用户问题拆成最多 3 个可以独立检索的子问题。

要求：
- 如果原问题只涉及一件事，就只输出一条。
- 每行一条，不要编号，不要解释。

用户问题：{q}
"""

CLARIFY_PROMPT = """你要判断下面这个问题能不能直接去知识库里检索。

知识库覆盖两个服务：login_demo（登录会话）和 pay_gateway（支付会话），
两者的会话有效期不同。如果问题里的“会话”“有效期”没有指明是哪一个服务，
检索就会同时命中两边，答出来可能张冠李戴。

判断规则：
- 如果问题在知识库范围内指向唯一对象，输出：明确
- 如果需要用户补充才能确定，输出：需要澄清
- 如果需要澄清，再写一行最简短的追问。
- 只输出这两行，不要解释。

用户问题：{q}
"""


def _clean_lines(text: str, limit: int) -> list[str]:
    """模型偶尔会把代码块围栏或空 markdown 一起吐出来，这里过滤掉。"""
    out = []
    for raw in text.strip().splitlines():
        line = raw.strip().strip("-·*\t").strip()
        line = line.strip('"“”')
        if not line or line in {"```", "=", "：", ":"}:
            continue
        if line.startswith("```") or set(line) <= {"`", "=", " "}:
            continue
        if line.endswith("："):
            continue
        out.append(line)
    return out[:limit]


def rewrite(llm, question: str) -> str:
    text, _ = ask(llm, REWRITE_PROMPT.format(q=question))
    lines = _clean_lines(text, 1)
    return lines[0] if lines else question


def multi_query(llm, question: str, n: int = 3) -> list[str]:
    text, _ = ask(llm, MULTI_PROMPT.format(q=question, n=n))
    return _clean_lines(text, n) or [question]


def decompose(llm, question: str) -> list[str]:
    text, _ = ask(llm, DECOMPOSE_PROMPT.format(q=question))
    return _clean_lines(text, 3) or [question]


def clarify(llm, question: str) -> tuple[str, str]:
    """返回 (判断, 追问句)。判断是“明确”或“需要澄清”。"""
    text, _ = ask(llm, CLARIFY_PROMPT.format(q=question))
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    verdict = lines[0] if lines else "明确"
    follow = lines[1] if len(lines) > 1 else ""
    return ("需要澄清" if "需要澄清" in verdict else "明确"), follow


# 项目术语表：左边是用户口语，右边是文档里真实出现的写法。
# 这份表要由熟悉项目的人维护；它只补词，不改句子结构。
GLOSSARY = {
    "登出": ["退出登录", "logout"],
    "退出": ["退出登录", "logout"],
    "掉线": ["登录状态老是掉"],
    "命令": ["测试入口", "python -m pytest"],
    "多久": ["有效期", "分钟"],
    "过期": ["到期"],
    "会话": ["session"],
    "常量": ["SESSION_TTL_SECONDS"],
    "续": ["续期"],
    "改哪些文件": ["需要改的地方"],
}


def expand_with_glossary(question: str) -> str:
    """把问题里命中的口语词，替换成文档里真正使用的术语，拼成一条扩展查询。"""
    extra: list[str] = []
    for spoken, terms in GLOSSARY.items():
        if spoken in question:
            extra.extend(t for t in terms if t not in question)
    if not extra:
        return question
    return question + " " + " ".join(dict.fromkeys(extra))
