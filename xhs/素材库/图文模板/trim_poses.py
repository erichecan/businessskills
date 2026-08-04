#!/usr/bin/env python3
"""裁掉姿势图的透明边距 —— 卡片里人物显得小，主因不是分辨率低，是四周空白。

2026-08-03 实测：pose7_面试对坐 画布 246×410，人物实际只占 234×255，底部 155px
全是透明（38%）；pose19_谈薪拉扯 空白占 49%。card.html 的 max-height:460px 约束的是
**含空白的整张图**，人物于是只剩一半高。裁掉空白后，同样的 max-height 下人物能大 40-100%，
而且完全不损失清晰度 —— 不是放大，是把白边去掉。

原始文件在 _原图备份/ 里（未裁剪版），随时可回退。

用法：
  python3 trim_poses.py --dry-run   # 只报会裁多少，不写文件
  python3 trim_poses.py             # 就地裁剪 pose*.png 与 ip.png
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("需要 Pillow：pip3 install Pillow")

DIR = Path(__file__).parent
PAD = 4  # 留一点边，避免线稿贴边被卡片圆角/缩放切到


def trim(p: Path, dry: bool):
    im = Image.open(p).convert("RGBA")
    bb = im.split()[3].getbbox()          # 按 alpha 求内容边界
    if not bb:
        return None
    w, h = im.size
    l, t, r, b = bb
    l, t = max(0, l - PAD), max(0, t - PAD)
    r, b = min(w, r + PAD), min(h, b + PAD)
    if (r - l, b - t) == (w, h):
        return None                        # 本来就没有空白
    if not dry:
        im.crop((l, t, r, b)).save(p)
    return (w, h), (r - l, b - t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(DIR.glob("pose*.png")) + [DIR / "ip.png"]
    changed = 0
    print(f"{'文件':28}{'原':>11}  →{'裁后':>11}   高度增益")
    for f in files:
        if not f.exists():
            continue
        r = trim(f, args.dry_run)
        if not r:
            continue
        (w, h), (nw, nh) = r
        # 卡片里是 max-height 在约束，所以关心的是高度方向省掉多少
        gain = h / nh
        print(f"{f.name:28}{w:>4}×{h:<4}  → {nw:>4}×{nh:<4}   ×{gain:.2f}")
        changed += 1
    print(f"\n{'[dry-run] 将裁剪' if args.dry_run else '已裁剪'} {changed}/{len(files)} 张"
          f"（原图在 _原图备份/，可回退）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
