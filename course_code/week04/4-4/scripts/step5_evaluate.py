"""第五步：验证效果。

输入：8 个真实问题 + 四种流水线配置。
动作：每种配置端到端跑一遍，按同一口径统计召回率、命中率、答案正确率、
      引用合格率、无依据实体比例、延迟和 token 消耗；
      再把答错的题分成“检索失败”和“生成失败”。
结果：标准输出给出四张表：总览、逐题、失败归因、成本。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.answer import assemble, build_prompt, label, verify
from rag.evaluate import fact_score, hit_at_k, load_questions, matches, recall_at_k
from rag.ingest import build_index, chunk, get_embedding, load_markdown_and_code
from rag.local_embed import BGESmallZhEmbedding
from rag.minimax import CALL_STATS, ask, build_llm
from rag.retrieve import BM25Retriever, rrf_fuse
from rag.rewrite import expand_with_glossary
from step4_postprocess import compress, llm_rerank

TOP_K = 3
CAND = 20
WEIGHTS = [0.7, 0.3]

CONFIGS = ["V0 最小闭环", "V1 检索优化", "V2 +Rerank", "V3 +Rerank+压缩"]


def main() -> None:
    questions = load_questions()
    nodes = chunk(load_markdown_and_code(), "markdown", 384)
    bm25 = BM25Retriever(nodes)
    llm = build_llm(max_tokens=600)
    llm_small = build_llm(max_tokens=300)

    t_build = time.time()
    v0_index = build_index(nodes, collection="login_demo_v0", overwrite=True,
                           embed_model=get_embedding())
    v0_build = time.time() - t_build
    t_build = time.time()
    bge_index = build_index(nodes, collection="login_demo_bge", overwrite=True,
                           embed_model=BGESmallZhEmbedding(), dim=512)
    bge_build = time.time() - t_build
    print(f"[建库] V0（MiniMax 向量）{v0_build:.1f}s   V1-V3（BGE 本地 + BM25）{bge_build:.1f}s")

    results = {name: [] for name in CONFIGS}
    print("\n" + "=" * 104)
    print("逐题执行")
    print("=" * 104)

    for q in questions:
        question = q["question"]
        row = {"id": q["id"]}

        # ---- V0：向量 top3 直出
        t0 = time.time()
        hits0 = v0_index.as_retriever(similarity_top_k=TOP_K).retrieve(question)
        ans0, usage0 = ask(llm, build_prompt(question, assemble(hits0)))
        sec0 = time.time() - t0

        # ---- V1：术语扩展 + BGE/BM25 加权融合
        t0 = time.time()
        eq = expand_with_glossary(question)
        hits1 = rrf_fuse([
            bge_index.as_retriever(similarity_top_k=CAND).retrieve(eq),
            bm25.retrieve(eq, CAND),
        ], top_k=TOP_K, weights=WEIGHTS)
        ans1, usage1 = ask(llm, build_prompt(question, assemble(hits1)))
        sec1 = time.time() - t0

        # ---- V2：在候选 20 上重排
        t0 = time.time()
        cands = rrf_fuse([
            bge_index.as_retriever(similarity_top_k=CAND).retrieve(eq),
            bm25.retrieve(eq, CAND),
        ], top_k=CAND, weights=WEIGHTS)
        hits2, _, _ = llm_rerank(llm_small, question, cands, TOP_K)
        ans2, usage2 = ask(llm, build_prompt(question, assemble(hits2)))
        sec2 = time.time() - t0

        # ---- V3：重排 + 上下文压缩
        t0 = time.time()
        hits3 = compress(llm_small, question, hits2)
        ans3, usage3 = ask(llm, build_prompt(question, assemble(hits3)))
        sec3 = time.time() - t0

        for name, hits, ans, usage, sec in [
            ("V0 最小闭环", hits0, ans0, usage0, sec0),
            ("V1 检索优化", hits1, ans1, usage1, sec1),
            ("V2 +Rerank", hits2, ans2, usage2, sec2),
            ("V3 +Rerank+压缩", hits3, ans3, usage3, sec3),
        ]:
            f = fact_score(ans, q["required_facts"], q["forbidden_facts"])
            v = verify(ans, hits)
            gold_in = any(matches(h, g) for h in hits for g in q["gold"]) if hits else False
            if f["correct"] and gold_in:
                failure = "通过"
            elif not gold_in:
                failure = "检索失败"
            else:
                failure = "生成失败"
            results[name].append({
                "id": q["id"],
                "recall": recall_at_k(hits, q["gold"]),
                "hit": hit_at_k(hits, q["gold"]),
                "correct": f["correct"],
                "missing": f["missing"],
                "forbidden": f["forbidden"],
                "verdict": v["verdict"],
                "unsupported": len(v["unsupported"]),
                "entities": len(v["supported"]) + len(v["unsupported"]),
                "failure": failure,
                "seconds": sec,
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "top1": label(hits[0]) if hits else "(空)",
                "answer": ans,
            })

        print(f"  {q['id']} " + "  ".join(
            f"{name.split()[0]}:{'√' if results[name][-1]['correct'] else '×'}"
            f"/{results[name][-1]['failure']}" for name in CONFIGS))

    n = len(questions)
    print("\n" + "=" * 104)
    print("① 总览（统计口径见各处括号）")
    print("=" * 104)
    header = ["配置", "召回率@3", "命中率@3", "答案正确率", "引用合格率",
              "含无依据实体", "平均延迟s", "平均prompt"]
    widths = [16, 10, 10, 11, 11, 13, 11, 11]
    print("  " + "".join(h.ljust(w) for h, w in zip(header, widths)))
    for name in CONFIGS:
        rows = results[name]
        vals = {
            "配置": name,
            "召回率@3": round(sum(r["recall"] for r in rows) / n, 3),
            "命中率@3": round(sum(r["hit"] for r in rows) / n, 3),
            "答案正确率": f"{sum(1 for r in rows if r['correct'])}/{n}",
            "引用合格率": f"{sum(1 for r in rows if r['verdict'] == '通过')}/{n}",
            "含无依据实体": f"{sum(1 for r in rows if r['unsupported'])}/{n}",
            "平均延迟s": round(sum(r["seconds"] for r in rows) / n, 1),
            "平均prompt": round(sum(r["prompt_tokens"] for r in rows) / n),
        }
        print("  " + "".join(str(vals[h]).ljust(w) for h, w in zip(header, widths)))

    print("\n" + "=" * 104)
    print("② 失败归因：答错的题是检索没找到，还是找到却没答对")
    print("=" * 104)
    for name in CONFIGS:
        rows = results[name]
        buckets = {"通过": [], "检索失败": [], "生成失败": []}
        for r in rows:
            buckets[r["failure"]].append(r["id"])
        print(f"  {name:<16} 通过={buckets['通过'] or []}")
        print(f"  {'':<16} 检索失败={buckets['检索失败'] or []}"
              f"   生成失败={buckets['生成失败'] or []}")

    print("\n  检索失败题的细节（gold 是否进了最终上下文）：")
    for name in CONFIGS:
        for r in results[name]:
            if r["failure"] == "检索失败":
                print(f"    {name} / {r['id']}：top1={r['top1']}  缺的必备事实={r['missing']}")

    print("\n  生成失败题的细节（gold 在上下文里，但答案没写全）：")
    seen = set()
    for name in CONFIGS:
        for r in results[name]:
            if r["failure"] == "生成失败" and (name, r["id"]) not in seen:
                seen.add((name, r["id"]))
                print(f"    {name} / {r['id']}：缺的必备事实={r['missing']} 引用判定={r['verdict']}")

    print("\n" + "=" * 104)
    print("③ 逐题对照（召回率 / 是否答对 / 引用判定）")
    print("=" * 104)
    print("  " + "题号".ljust(6) + "".join(name.ljust(30) for name in CONFIGS))
    for q in questions:
        cells = []
        for name in CONFIGS:
            r = next(x for x in results[name] if x["id"] == q["id"])
            cells.append(f"{r['recall']} {'√' if r['correct'] else '×'} {r['verdict']}")
        print("  " + q["id"].ljust(6) + "".join(c.ljust(30) for c in cells))

    print("\n" + "=" * 104)
    print("④ 成本：这次跑完实际发生多少调用")
    print("=" * 104)
    print(f"  MiniMax embedding：{CALL_STATS['embed_calls']} 次请求、"
          f"{CALL_STATS['embed_texts']} 条文本、{CALL_STATS['embed_seconds']:.1f}s")
    for name in CONFIGS:
        rows = results[name]
        print(f"  {name:<16} prompt={sum(r['prompt_tokens'] for r in rows):>6} tokens  "
              f"completion={sum(r['completion_tokens'] for r in rows):>5} tokens  "
              f"端到端={sum(r['seconds'] for r in rows):.1f}s")


if __name__ == "__main__":
    main()
