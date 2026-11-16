"""评估口径：召回率、命中率、答案正确率、幻觉率。

统一在这里定义，第五步换配置重跑时口径不变，数字才可比。
"""

from __future__ import annotations

import json
from typing import Iterable, Sequence

from .answer import label
from .config import EVAL
from .minimax import CALL_STATS


def load_questions(path=None) -> list[dict]:
    path = path or (EVAL / "questions.json")
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def matches(hit_or_node, gold: dict) -> bool:
    node = getattr(hit_or_node, "node", hit_or_node)
    if node.metadata.get("file_path") != gold["file"]:
        return False
    want = gold.get("heading") or ""
    if not want:
        return True
    return want in (node.metadata.get("heading") or "")


def recall_at_k(hits: Sequence, gold: Sequence[dict]) -> float:
    """召回率：gold 证据里有多少条出现在检索结果中。"""
    if not gold:
        return 0.0
    found = sum(1 for g in gold if any(matches(h, g) for h in hits))
    return found / len(gold)


def hit_at_k(hits: Sequence, gold: Sequence[dict]) -> float:
    """命中率：只要 gold 里有一条进了结果就算 1。"""
    return 1.0 if any(matches(h, g) for h in hits for g in gold) else 0.0


def fact_score(answer: str, required: Iterable[str], forbidden: Iterable[str]) -> dict:
    """答案正确率：必须出现的都出现、禁止出现的都不出现，才算对。"""
    req = list(required)
    miss = [f for f in req if f not in answer and f.replace(" ", "") not in answer.replace(" ", "")]
    bad = [f for f in forbidden if f in answer]
    return {"correct": not miss and not bad, "missing": miss, "forbidden": bad}


def hallucination_rate(verifications: Sequence[dict]) -> float:
    """幻觉率：回答里被引用证据找不到的关键实体，占全部关键实体的比例。"""
    total = sum(len(v["supported"]) + len(v["unsupported"]) for v in verifications)
    if total == 0:
        return float("nan")
    return sum(len(v["unsupported"]) for v in verifications) / total


def embed_calls() -> int:
    return CALL_STATS["embed_calls"]


def summarize(rows: Sequence[dict]) -> dict:
    n = len(rows)
    return {
        "问题数": n,
        "召回率": round(sum(r["recall"] for r in rows) / n, 3),
        "命中率": round(sum(r["hit"] for r in rows) / n, 3),
        "答案正确率": round(sum(1 for r in rows if r.get("correct")) / n, 3),
        "引用合格率": round(sum(1 for r in rows if r.get("citation_ok")) / n, 3),
        "平均延迟s": round(sum(r["seconds"] for r in rows) / n, 2),
        "平均prompt_tokens": round(sum(r["prompt_tokens"] for r in rows) / n),
    }


def retrieved_labels(hits) -> list[str]:
    return [label(h) for h in hits]
