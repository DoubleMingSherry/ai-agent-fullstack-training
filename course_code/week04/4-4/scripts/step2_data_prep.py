"""第二步：完善数据准备。

输入：corpus 下的 Markdown、Python、HTML、CSV、PDF。
动作：① 解析 PDF/HTML/表格并清洗；② 用六种分块策略切同一批文档；
      ③ 对比节点数、长度、标题覆盖率和函数完整率；④ 演示 overlap；⑤ 看一个节点的完整元数据。
结果：标准输出里给出解析前后的文本片段和一张分块对比表。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.ingest import (CORPUS, chunk, clean_text, get_embedding, load_all,
                        load_html, load_pdf, load_table, parse_front_matter)


def brief(text: str, n: int = 90) -> str:
    text = " / ".join(line for line in text.splitlines() if line.strip())
    return text[:n] + ("…" if len(text) > n else "")


def code_functions() -> list[tuple[str, str]]:
    """列出三个源码文件里的每个函数：(文件名, 函数源码)。"""
    out = []
    for path in sorted((CORPUS / "app").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        for item in ast.parse(src).body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = "\n".join(lines[item.lineno - 1:item.end_lineno]).strip()
                out.append((path.name, seg))
    return out


FUNCS = code_functions()


def stats(nodes, name: str) -> dict:
    lens = [len(n.text) for n in nodes]
    titled = sum(1 for n in nodes if n.metadata.get("heading"))
    whole = sum(1 for _, fn in FUNCS if any(fn in n.text for n in nodes))
    return {
        "策略": name,
        "节点数": len(nodes),
        "平均字符": round(sum(lens) / len(lens)) if lens else 0,
        "最长": max(lens) if lens else 0,
        "带标题": f"{titled}/{len(nodes)}",
        "函数完整": f"{whole}/{len(FUNCS)}",
    }


def main() -> None:
    print("=" * 78)
    print("① 解析：四种格式分别读成什么")
    print("=" * 78)
    raw_pdf = load_pdf(CORPUS / "pdf" / "PRD-login-2.5.0.pdf")
    print(f"[PDF ] 原始抽取 {len(raw_pdf)} 字符，开头：{brief(raw_pdf)}")
    print(f"[PDF ] 清洗后   {len(clean_text(raw_pdf))} 字符，开头：{brief(clean_text(raw_pdf))}")

    raw_html = (CORPUS / "web" / "release-notes-2.4.0.html").read_text(encoding="utf-8")
    print(f"\n[HTML] 原文含标签 {len(raw_html)} 字符，开头：{brief(raw_html)}")
    html = load_html(CORPUS / "web" / "release-notes-2.4.0.html")
    print(f"[HTML] 解析后     {len(html)} 字符，开头：{brief(html)}")

    csv_raw = (CORPUS / "tables" / "session-matrix.csv").read_text(encoding="utf-8")
    print(f"\n[CSV ] 原文 {len(csv_raw.splitlines())} 行，第 1 行：{csv_raw.splitlines()[0]}")
    table = load_table(CORPUS / "tables" / "session-matrix.csv")
    print(f"[CSV ] 解析后 {len(table.splitlines())} 句，第 1 句：{brief(table, 120)}")

    print("\n" + "=" * 78)
    print("② 元数据：每个文档带上什么标签")
    print("=" * 78)
    docs = load_all()
    print(f"共 {len(docs)} 个文档")
    for d in docs:
        print(f"  {d.metadata['file_path']:<38} doc_type={d.metadata['doc_type']:<6} source={d.metadata['source']}")

    print("\n" + "=" * 78)
    print("③ 分块对比：同一批文档，六种切法")
    print("=" * 78)
    emb = get_embedding()
    # llama-index 会把元数据长度算进 chunk_size 预算，元数据一长就打印提示。
    # 这些提示本身是结论的一部分，但不能让它们散落在表格中间，所以先收起来再统一打印。
    warnings: list[str] = []
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rows = [
            stats(chunk(docs, "fixed", chunk_size=96, overlap=0), "固定长度 96 token 无重叠"),
            stats(chunk(docs, "fixed", chunk_size=96, overlap=24), "固定长度 96 token 重叠 24"),
        ]
        warnings = buf.getvalue().strip().splitlines()
    rows += [
        stats(chunk(docs, "paragraph", chunk_size=200), "按段落 ≤200 字符"),
        stats(chunk(docs, "markdown", chunk_size=384, overlap=0), "按标题"),
        stats(chunk(docs, "semantic", embed_model=emb), "语义分块"),
    ]
    ast_nodes = chunk([d for d in docs if d.metadata["doc_type"] == "code"], "ast")
    rows.append(stats(ast_nodes, "代码 AST"))
    if warnings:
        print(f"[提示] llama-index 报了 {len(warnings)} 次：{warnings[0]}")
        print("       front-matter 让每个节点的元数据长到 50 字符左右，")
        print("       它会被算进 chunk_size 预算，所以 96 token 的块实际只剩 40 多个 token。")
    width = [20, 8, 10, 8, 9, 12]
    header = ["策略", "节点数", "平均字符", "最长", "带标题", "函数完整"]
    print("  " + "".join(h.ljust(w) for h, w in zip(header, width)))
    for r in rows:
        print("  " + "".join(str(r[h]).ljust(w) for h, w in zip(header, width)))
    longest = max((n for n in chunk(docs, "markdown", 384)), key=lambda n: len(n.text))
    print(f"\n  最长节点来自 {longest.metadata['file_path']}"
          f"（{len(longest.text)} 字符，标题={longest.metadata['heading']}）")

    print("\n" + "=" * 78)
    print("④ Overlap：用字符窗口把切断的句子接回来")
    print("=" * 78)
    section = (
        "## 到期边界\n\n"
        "- 项目约定：当前时刻恰好等于到期时刻时，会话必须判定为无效。\n"
        "- 也就是说，`now == expires_at` 属于已过期。\n"
        "- 实现上有效条件写作 `now < expires_at`，不要写成 `now <= expires_at`。\n"
    )

    def by_chars(text: str, size: int, overlap: int) -> list[str]:
        step = size - overlap
        return [text[i:i + size] for i in range(0, len(text), step) if text[i:i + size]]

    no_ov = by_chars(section, 40, 0)
    with_ov = by_chars(section, 40, 15)
    needle = "不要写成 `now <= expires_at`"
    print(f"原文 {len(section)} 字符，窗口 40 字符")
    print(f"[无重叠 overlap=0 ] 切出 {len(no_ov)} 块，第 1 块结尾：{no_ov[0][-24:]!r}")
    print(f"                    含“{needle}”？{'是' if any(needle in c for c in no_ov) else '否'}")
    print(f"[有重叠 overlap=15] 切出 {len(with_ov)} 块")
    hit = next((i for i, c in enumerate(with_ov) if needle in c), -1)
    if hit >= 0:
        print(f"                    含“{needle}”的块是第 {hit + 1} 块：{with_ov[hit]!r}")

    # 同一段真实文档，两种切法各切出多少块。
    # 注意要读正文，不能读原始文件——原始文件顶部的 front-matter 不参与分块。
    _, real = parse_front_matter((CORPUS / "docs" / "session-rules.md").read_text(encoding="utf-8"))
    print(f"\n换到真实的 docs/session-rules.md 正文（{len(real)} 字符，已去掉 front-matter）：")
    print(f"  overlap=0  切成 {len(by_chars(real, 60, 0))} 块，"
          f"块数 × 窗口 = {len(by_chars(real, 60, 0)) * 60} 字符")
    print(f"  overlap=20 切成 {len(by_chars(real, 60, 20))} 块，"
          f"块数 × 窗口 = {len(by_chars(real, 60, 20)) * 60} 字符")

    print("\n" + "=" * 78)
    print("⑤ Chunk Metadata：一个节点的完整元数据")
    print("=" * 78)
    md_nodes = chunk(docs, "markdown")
    sample = next(n for n in md_nodes
                  if n.metadata["file_path"] == "docs/testing-guide.md"
                  and "测试入口" in (n.metadata.get("heading") or ""))
    print(json.dumps({"id": sample.node_id, "metadata": sample.metadata,
                      "text_chars": len(sample.text)}, ensure_ascii=False, indent=2))

    code_sample = next(n for n in ast_nodes if n.metadata["file_path"] == "app/auth.py")
    print("\n代码节点的元数据：")
    print(json.dumps({"metadata": code_sample.metadata, "text": code_sample.text[:80]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
