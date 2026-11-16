"""Evaluate retrieved evidence; answer labels must be supplied by a reviewer."""
import json
from pathlib import Path
from statistics import mean
from rag import ROOT, CANDIDATE_K, load_nodes

nodes = load_nodes()
def ids(*headings):
    return sorted(n.node_id for n in nodes if any(n.text.startswith("## " + h + "\n") for h in headings))

GOLD = {
    "会话恰好到期还有效，要检查哪里，应该怎样判断和测试？": ids("模块入口", "到期边界规则", "测试入口"),
    "is_session_valid 的边界规则是什么？": ids("模块入口", "到期边界规则"),
    "改完登录会话以后怎么验证？": ids("测试入口"),
    "退出登录失败是不是 Redis 导致的？": ids("会话撤销"),
    "这个项目的生产部署地址是什么？": [],
}

def main():
    trace = ROOT / "traces.jsonl"
    entries = [json.loads(s) for s in trace.read_text().splitlines()] if trace.exists() else []
    label_path = ROOT / "labels.json"
    labels = json.loads(label_path.read_text()) if label_path.exists() else {}
    answers = {x["run_id"]: x for x in entries if x["event"] == "answer"}
    # Keep latest run per question/mode to prevent repeated runs changing sample weights.
    runs = {(x["mode"], x["question"]): x for x in entries
            if x["event"] == "retrieval" and x["question"] in GOLD}
    if not runs:
        print("尚无评估问题的检索记录，请先运行 run_questions.py")
    for mode in sorted({m for m, _ in runs}):
        group = [r for (m, _), r in runs.items() if m == mode]
        recalls, hits, latencies, correct, supported, counts = [], [], [], [], 0, 0
        diagnostics = []
        for r in group:
            gold, candidates = set(GOLD[r["question"]]), set(r["candidates"])
            final = {e["node_id"] for e in r["evidence"]}
            if gold:
                recalls.append(len(gold & candidates) / len(gold))
                hits.append(int(bool(gold & candidates)))
            latencies.append(r["latency_s"])
            label = labels.get(r["run_id"], {})
            if r["run_id"] in answers and isinstance(label.get("answer_correct"), bool):
                correct.append(int(label["answer_correct"]))
                supported += label.get("unsupported_claims", 0)
                counts += label.get("factual_claims", 0)
            state = "未完成人工答案标注"
            if gold and not gold <= candidates: state = "召回不完整"
            elif gold and not gold <= final: state = "装配遗漏证据"
            elif label.get("answer_correct") is False: state = "证据齐全但答案不合格，检查生成与引用"
            elif label.get("answer_correct") is True: state = "人工验收通过"
            diagnostics.append({"run_id": r["run_id"], "question": r["question"], "state": state})
        print(json.dumps({"mode": mode, "retrieval_questions": len(group),
            "positive_questions": len(recalls), f"recall_at_{CANDIDATE_K}": mean(recalls) if recalls else None,
            f"hit_at_{CANDIDATE_K}": mean(hits) if hits else None, "answer_labeled_count": len(correct),
            "answer_correct_rate": mean(correct) if correct else None,
            "unsupported_claim_rate": supported / counts if counts else None,
            "retrieval_selection_latency_mean_s": mean(latencies), "diagnostics": diagnostics},
            ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
