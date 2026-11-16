"""生成一份 PDF 需求说明，作为第二步的 PDF 解析素材。

这份 PDF 用 Arial Unicode 字体渲染中文，pypdf 抽出来是可复制文字（不是扫描件）。
"""

from fpdf import FPDF

from rag.config import CORPUS

FONT = "/Library/Fonts/Arial Unicode.ttf"

CONTENT = [
    ("h1", "login_demo 产品需求说明 v2.5.0（草稿）"),
    ("p", "文档编号 PRD-LOGIN-025，维护人：认证组。本说明用于规划退出登录功能。"),
    ("h2", "1. 目标"),
    ("p", "用户可以在任意页面点击“退出登录”，服务端立即让该会话令牌失效，"
          "不需要等待 30 分钟自然到期。"),
    ("h2", "2. 令牌失效的判定"),
    ("p", "退出登录之后，同一个令牌再次调用 check() 必须返回 False。"
          "这条和“到期边界”规则一致：只要不再满足 now < expires_at，就判定为无效。"),
    ("h2", "3. 涉及模块"),
    ("p", "app/auth.py 提供 logout() 入口；app/session.py 保存有效期的常量 SESSION_TTL_SECONDS。"
          "两个文件的职责不要混：auth 负责令牌生命周期，session 负责时间判定。"),
    ("h2", "4. 验收标准"),
    ("p", "运行 python -m pytest tests/test_session.py -q，全部用例通过，"
          "其中新增用例名为 test_logout_invalidates。"),
    ("h2", "5. 不做的事情"),
    ("p", "本次不清理浏览器端缓存，也不修改记住我令牌的有效期。"),
]


def main() -> None:
    pdf = FPDF(format="A4")
    pdf.add_font("cjk", "", FONT)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    for kind, text in CONTENT:
        if kind == "h1":
            pdf.set_font("cjk", size=17)
            pdf.multi_cell(0, 10, text)
            pdf.ln(2)
        elif kind == "h2":
            pdf.set_font("cjk", size=13)
            pdf.ln(2)
            pdf.multi_cell(0, 8, text)
            pdf.ln(1)
        else:
            pdf.set_font("cjk", size=11)
            pdf.multi_cell(0, 7, text)
            pdf.ln(1)
    out = CORPUS / "pdf" / "PRD-login-2.5.0.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    print("written:", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
