#!/usr/bin/env python3
"""图文卡片渲染：JSON → 7 张 1242×1660 PNG（headless Chrome，零 AI 生图成本）。

输入 JSON 数组，每项：{"type":"cover|scene|contrast|quote|why|formula|boundary",
 "tag":"眉标","title":"大字（可用<span class='hl'>标红</span>）","quote":"原话（可空）","body":"正文（可空）"}
用法：python3 make_cards.py 内容.json 输出目录/
"""
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TPL = (Path(__file__).parent / "card.html").read_text(encoding="utf-8")
IP_IMG = Path(__file__).parent / "ip.png"  # IP 形象（存在则自动上封面）
# 版面预算：按**渲染后的估算行数**算，不是按字数。
# 字数会骗人 —— 实测某张卡 110 字（未超字数阈值），但每行都写得很长，
# 折行后 body 实际占 7 行，人物被压到 60px 高，等于白放。
# 每行容纳字数 ≈ (1242 - 左右padding176) / 字号；行高按字号×行距折算成权重。
CHARS_PER_LINE = {"title": 10, "quote": 14, "body": 19}   # 字号 104 / 72 / 54
LINE_WEIGHT = {"title": 1.5, "quote": 1.15, "body": 1.0}  # 行高相对 body 的倍数
# 等效行数上限。10.0 太松 —— 实测 9.9 行那张人物只剩约 100px；
# 8.8 行那张约 200px 尚可，所以卡在 9.0。
LINE_BUDGET = 9.0
POSE_MAP = {  # 卡型 → **兜底**姿势。正常情况下卡片 JSON 的 "pose" 字段会覆盖它 —— 
# 只靠这张表的话，每篇笔记 7 张图的姿势完全相同（卡型顺序是固定的），
# 30 个姿势里只用得到下面这 9 个。2026-08-03 起由成稿按内容逐卡指定。
    "scene": "pose7_面试对坐", "contrast": "pose3_摊手", "why": "pose4_沉思",
    "formula": "pose5_白板", "boundary": "pose11_打勾打叉", "quote": "pose8_被追问冒汗",
    "coverbig": "pose1_站立", "covertalk": "pose9_推眼镜反击", "coversplit": "pose5_白板",
}


def est_lines(card) -> float:
    """估算这张卡的文字占多少「等效行」。<br> 手动换行和自动折行都要算进去。"""
    total = 0.0
    for field in ("title", "quote", "body"):
        text = re.sub(r"<(?!br)[^>]+>", "", card.get(field, ""))
        if not text:
            continue
        per = CHARS_PER_LINE[field]
        lines = sum(max(1, -(-len(seg) // per)) for seg in text.split("<br>"))
        total += lines * LINE_WEIGHT[field]
    return total


def render(cards, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(cards, 1):
        quote = c.get("quote", "")
        qb = f'<div class="quote">「{html.escape(quote)}」</div>' if quote else ""
        ip = (f'<img class="ipimg" src="file://{IP_IMG}">'
              if c.get("type") == "cover" and IP_IMG.exists() else "")
        # cover 卡右下角已经有圆形 IP 头像，再挂一张大姿势图就成了一张卡上两个人。
        # 2026-08-04 Eric 定：封面保持右下角圆形图，不放第二张。
        # （成稿现在会给每张卡都指定 pose，所以这里必须显式挡掉 cover。）
        ctype = c.get("type", "")
        pose_name = "" if ctype == "cover" else (c.get("pose") or POSE_MAP.get(ctype, ""))
        # 版面守恒：文字占满时人物会被 flex 压成一个点。
        # 与其放个看不清的小人，不如不放，并把超标的卡报出来让成稿改短。
        if pose_name and est_lines(c) > LINE_BUDGET:
            print(f"   ⚠️ 第{i}张文案约 {est_lines(c):.1f} 等效行（上限 {LINE_BUDGET}），"
                  f"人物会被挤没，本张不放姿势图")
            pose_name = ""
        pose_file = Path(__file__).parent / f"{pose_name}.png"
        pose = (f'<img class="poseimg" src="file://{pose_file}">'
                if pose_name and pose_file.exists() else "")
        page = (TPL.replace("{{POSE_BLOCK}}", pose)
                   .replace("{{IP_BLOCK}}", ip)
                   .replace("{{TYPE}}", c.get("type", ""))
                   .replace("{{TAG}}", html.escape(c.get("tag", "")))
                   .replace("{{IDX}}", str(i))
                   .replace("{{TITLE}}", c.get("title", ""))
                   .replace("{{QUOTE_BLOCK}}", qb)
                   .replace("{{BODY}}", html.escape(c.get("body", "")).replace("&lt;br&gt;", "<br>")))
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(page)
            tmp = f.name
        out = outdir / f"{i:02d}_{c.get('type','card')}.png"
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--force-device-scale-factor=1",
                        f"--window-size=1242,1660", f"--screenshot={out}", f"file://{tmp}"],
                       check=True, capture_output=True, timeout=60)
        print(f"✅ {out.name}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), Path(sys.argv[2]))
