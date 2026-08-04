#!/usr/bin/env python3
"""IP 形象去白底 — 把与四角连通的白色区域变透明。

为什么不能直接全局「白 → 透明」：这批是白底线稿，人物的脸、衬衫、纸张内部
也大片是白的。全局替换会把人物掏空。所以只做泛洪：从四个角出发，
沿着相邻像素扩散，遇到线条（非白）就停——只有背景会被清掉。

用法：
  python3 make_transparent.py            # 处理目录下所有 pose*.png 与 ip.png
  python3 make_transparent.py --check    # 只报告哪些图还没有 alpha，不改文件
备份写在 _原图备份/ 下，可随时还原。
"""
import argparse
import shutil
import sys
from collections import deque
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
BACKUP = HERE / "_原图备份"
WHITE_MIN = 233           # 亮于此值即视为背景候选（线稿抗锯齿边缘会略灰）
FEATHER_MIN = 200         # 200~233 之间的半透明过渡，避免锯齿硬边


def targets():
    return sorted([p for p in HERE.glob("*.png")
                   if p.name.startswith("pose") or p.name == "ip.png"])


def is_bg(px, i):
    r, g, b = px[i], px[i + 1], px[i + 2]
    return r >= WHITE_MIN and g >= WHITE_MIN and b >= WHITE_MIN


def strip_background(path: Path) -> str:
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    px = bytearray(im.tobytes())
    seen = bytearray(w * h)
    q = deque()

    for x in range(w):
        for y in (0, h - 1):
            q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            q.append((x, y))

    cleared = 0
    while q:
        x, y = q.popleft()
        if not (0 <= x < w and 0 <= y < h):
            continue
        n = y * w + x
        if seen[n]:
            continue
        seen[n] = 1
        i = n * 4
        if not is_bg(px, i):
            continue
        px[i + 3] = 0
        cleared += 1
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # 边缘羽化：紧挨透明区的浅灰像素按亮度给半透明，消除锯齿白边
    for n in range(w * h):
        i = n * 4
        if px[i + 3] == 0:
            continue
        r, g, b = px[i], px[i + 1], px[i + 2]
        lum = (r + g + b) / 3
        if lum <= FEATHER_MIN:
            continue
        x, y = n % w, n // w
        touching = False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and px[(ny * w + nx) * 4 + 3] == 0:
                touching = True
                break
        if touching:
            val = int(255 * (WHITE_MIN - lum) / (WHITE_MIN - FEATHER_MIN))
            px[i + 3] = max(0, min(255, val))

    BACKUP.mkdir(exist_ok=True)
    if not (BACKUP / path.name).exists():
        shutil.copy2(path, BACKUP / path.name)
    Image.frombytes("RGBA", (w, h), bytes(px)).save(path)
    return f"清除背景 {cleared} 像素（{cleared * 100 // (w * h)}%）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    files = targets()
    if not files:
        print("没找到 pose*.png / ip.png", file=sys.stderr)
        return 1

    if args.check:
        for p in files:
            im = Image.open(p)
            has = im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 255
            print(f"{'✅ 已透明' if has else '⛔ 白底  '} {p.name}")
        return 0

    for p in files:
        im = Image.open(p)
        if im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 0:
            print(f"跳过（已透明）{p.name}")
            continue
        print(f"{p.name:<28}{strip_background(p)}")
    print(f"\n完成 {len(files)} 张，原图备份在 {BACKUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
