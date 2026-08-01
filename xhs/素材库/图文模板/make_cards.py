#!/usr/bin/env python3
"""图文卡片渲染：JSON → 7 张 1242×1660 PNG（headless Chrome，零 AI 生图成本）。

输入 JSON 数组，每项：{"type":"cover|scene|contrast|quote|why|formula|boundary",
 "tag":"眉标","title":"大字（可用<span class='hl'>标红</span>）","quote":"原话（可空）","body":"正文（可空）"}
用法：python3 make_cards.py 内容.json 输出目录/
"""
import html
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TPL = (Path(__file__).parent / "card.html").read_text(encoding="utf-8")
IP_IMG = Path(__file__).parent / "ip.png"  # IP 形象（存在则自动上封面）
POSE_MAP = {  # 卡型 → 姿势插画（角色参与内容，不是装饰）
    "scene": "pose1_站立", "contrast": "pose3_摊手", "why": "pose4_沉思",
    "formula": "pose5_白板", "boundary": "pose6_叹气", "quote": "pose6_叹气",
}


def render(cards, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(cards, 1):
        quote = c.get("quote", "")
        qb = f'<div class="quote">「{html.escape(quote)}」</div>' if quote else ""
        ip = (f'<img class="ipimg" src="file://{IP_IMG}">'
              if c.get("type") == "cover" and IP_IMG.exists() else "")
        pose_file = Path(__file__).parent / f"{POSE_MAP.get(c.get('type', ''), '')}.png"
        pose = (f'<img class="poseimg" src="file://{pose_file}">'
                if c.get("type") in POSE_MAP and pose_file.exists() else "")
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
