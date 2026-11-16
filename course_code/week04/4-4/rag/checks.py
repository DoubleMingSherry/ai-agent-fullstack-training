"""从回答里抽出"可核对的关键实体"，用于 Citation 和一致性校验。

抽四类：文件路径、常量名、数字+单位、命令。
这四类都能在被引证据里按字面找到或找不到，所以可以自动判定。
"""

from __future__ import annotations

import re

PATTERNS = [
    ("file", re.compile(r"[A-Za-z_][\w/.-]*\.(?:py|md|html|csv|pdf|json)")),
    ("const", re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")),
    ("number", re.compile(r"\d+\s*(?:分钟|秒|小时|天)")),
    ("command", re.compile(r"python -m pytest[^\n，。；]*")),
    ("bool", re.compile(r"\b(?:True|False)\b")),
]


def extract_entities(text: str) -> list[tuple[str, str]]:
    """返回 [(类型, 实体文本)]，保持出现顺序并去重。"""
    found: list[tuple[str, str]] = []
    seen = set()
    for kind, pattern in PATTERNS:
        for m in pattern.findall(text):
            item = m.strip().strip("`").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            found.append((kind, item))
    return found


def check_support(answer: str, evidence_text: str) -> tuple[list, list]:
    """返回 (有依据的实体, 没依据的实体)。"""
    supported, unsupported = [], []
    for kind, item in extract_entities(answer):
        hit = item in evidence_text
        if not hit and kind == "number":
            # "30 分钟" 与 "30分钟" 都算命中
            hit = item.replace(" ", "") in evidence_text.replace(" ", "")
        (supported if hit else unsupported).append((kind, item))
    return supported, unsupported
