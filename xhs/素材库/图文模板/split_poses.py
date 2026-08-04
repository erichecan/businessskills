#!/usr/bin/env python3
"""把「单行三个姿势」的合集图切成单张 pose 图。

不用固定 3 等分：三个人物在画面里的间距并不均匀，固定切分点可能正好落在人物身上
（实测组 1 的第二个人物左边缘在 x≈520，而 1536/3 的分界线是 512，差 8px 就切到了）。
改成按 alpha 的列投影找空隙 —— 人物之间必然有一段完全透明的列，从那里切。

顺带完成 trim：每个切片按 alpha 边界裁掉四周空白，卡片里 poseimg 是按 height 约束的，
留着空白等于白白缩小人物。

用法：
  python3 split_poses.py --dry-run                 # 报告会切成什么、尺寸够不够，不写文件
  python3 split_poses.py                           # 按 GROUPS 映射切分并替换 pose*.png
  python3 split_poses.py --group 3 --src a.png     # 只处理某一组
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("需要 Pillow：pip3 install Pillow")

HERE = Path(__file__).parent
BACKUP = HERE / "_原图备份"
DOWNLOADS = Path.home() / "Downloads"
# 已切过的源图清单。不记的话，切完的图还留在下载目录里，下次仍算「最新一批」，
# 会被当成新组重切一遍并张冠李戴（实测：组 1-8 切完后再跑，它们被认成了组 9-16）。
STATE = HERE / ".split_state.json"

ALPHA_MIN = 12       # alpha 高于此值算「有内容」，滤掉 ChatGPT 生成的极淡光晕
MIN_BLOCK_W = 120    # 窄于此的内容块视为噪点（飘出来的问号、纸飞机等），并入相邻块
PAD = 4
# 验收只卡高度：卡片里 poseimg 是按 height 约束的，宽度不是瓶颈。
# 「站立」「摊手」这类纯人物姿势天生就窄（220px 也正常），按宽度判会误报。
MIN_H = 700

# 组号 → 三个姿势的文件名（左→右），对应 docs/20260803-高清姿势图生成提示词.md
GROUPS = {
    1: ["pose1_站立", "pose2_打字", "pose3_摊手"],
    2: ["pose4_沉思", "pose5_白板", "pose6_叹气"],
    3: ["pose7_面试对坐", "pose8_被追问冒汗", "pose9_推眼镜反击"],
    4: ["pose10_举牌", "pose11_打勾打叉", "pose12_握手"],
    5: ["pose13_深夜改简历", "pose14_电梯独白", "pose15_如释重负"],
    6: ["pose16_小小庆祝", "pose17_排队等待", "pose18_翻旧账"],
    7: ["pose19_谈薪拉扯", "pose20_躲甩锅", "pose21_反问时刻"],
    8: ["pose22_听潜台词", "pose23_读信号", "pose24_向上汇报"],
    9: ["pose25_幽默接话", "pose26_优缺点天平", "pose27_拎箱告别"],
    10: ["pose28_线上面试", "pose29_演讲开场", "pose30_记三行笔记"],
    11: ["pose31_等回复", "pose32_被孤立", "pose33_收到低绩效"],
    12: ["pose34_说话没人听", "pose35_被打断", "pose36_举手发言"],
    13: ["pose37_看表赶时间", "pose38_递交材料", "pose39_接电话"],
    14: ["pose40_屏幕前皱眉", "pose41_转身离开", "pose42_强撑微笑"],
}


def content_columns(alpha):
    """逐列判断有没有内容。用 alpha 通道，不看颜色 —— 线稿本身是黑的，按亮度判会误伤。"""
    w, h = alpha.size
    px = alpha.load()
    cols = []
    for x in range(w):
        hit = False
        for y in range(0, h, 3):          # 隔行采样，够用且快 3 倍
            if px[x, y] > ALPHA_MIN:
                hit = True
                break
        cols.append(hit)
    return cols


def find_blocks(cols):
    """把连续有内容的列合并成块，丢掉太窄的噪点块。"""
    blocks, start = [], None
    for x, has in enumerate(cols):
        if has and start is None:
            start = x
        elif not has and start is not None:
            blocks.append((start, x))
            start = None
    if start is not None:
        blocks.append((start, len(cols)))
    if not blocks:
        return []
    # 噪点（飘出的问号、平底锅）并进最近的大块，避免把一个人物拆成两块
    big = [b for b in blocks if b[1] - b[0] >= MIN_BLOCK_W]
    for s, e in blocks:
        if e - s >= MIN_BLOCK_W:
            continue
        near = min(big, key=lambda b: min(abs(b[0] - e), abs(s - b[1])))
        i = big.index(near)
        big[i] = (min(near[0], s), max(near[1], e))
    return sorted(big)


def column_density(alpha):
    w, h = alpha.size
    px = alpha.load()
    return [sum(1 for y in range(0, h, 3) if px[x, y] > ALPHA_MIN) for x in range(w)]


def split_by_valley(alpha, n=3):
    """找不到完全透明的空隙时的退化方案：在理论切分点附近找最稀疏的列。

    实测组 2（沉思/白板/叹气）就是这种情况 —— 第二个人物的白板一直延伸到
    接近第三个人物的马克杯，中间一列透明都不剩，按空隙分会把两个人当成一个。
    这时切在「内容最少」的那一列，顶多蹭掉道具边缘一点，比整组作废强。
    """
    w = alpha.size[0]
    dens = column_density(alpha)
    cuts = []
    for i in range(1, n):
        center = w * i // n
        lo, hi = max(1, center - w // 8), min(w - 1, center + w // 8)
        cuts.append(min(range(lo, hi), key=lambda x: dens[x]))
    bounds = [0] + cuts + [w]
    return [(bounds[i], bounds[i + 1]) for i in range(n)]


def split_one(src: Path, names, dry: bool):
    im = Image.open(src).convert("RGBA")
    alpha = im.split()[3]
    blocks = find_blocks(content_columns(alpha))
    how = "空隙分割"
    if len(blocks) != 3:
        blocks = split_by_valley(alpha, 3)
        how = f"⚠️ 只找到 {len(find_blocks(content_columns(alpha)))} 个块（道具粘连），改用最稀疏列分割"
    print(f"  [{how}]")
    ok = True
    for (x0, x1), name in zip(blocks, names):
        cell = im.crop((max(0, x0 - PAD), 0, min(im.width, x1 + PAD), im.height))
        bb = cell.split()[3].getbbox()
        if bb:
            cell = cell.crop(bb)
        w, h = cell.size
        flag = "✅" if h >= MIN_H else f"⚠️ 高度不足 {MIN_H}"
        print(f"  {name:22} {w:>4}×{h:<4} {flag}")
        if h < MIN_H:
            ok = False
        if not dry:
            dst = HERE / f"{name}.png"
            if dst.exists():
                BACKUP.mkdir(exist_ok=True)
                shutil.copy2(dst, BACKUP / f"{name}.png")
            cell.save(dst)
    return ok


def load_done() -> set:
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def mark_done(paths):
    STATE.write_text(json.dumps(sorted(load_done() | {p.name for p in paths}),
                                ensure_ascii=False, indent=1), encoding="utf-8")


def latest_batch(imgs, gap_hours=6):
    """按下载时间自动取「最新的一批」。

    下载目录里混着几个月的历史 ChatGPT 图，全都拿来会把组号整体排错
    （实测今天的第一张被编成组 9，切出来全部张冠李戴）。
    同一批图是连着几分钟内下载的，跟历史图之间必然隔着很长时间 ——
    从最新一张往回走，遇到超过 gap_hours 的断层就停。
    """
    if not imgs:
        return []
    imgs = sorted(imgs, key=lambda p: p.stat().st_mtime)
    batch = [imgs[-1]]
    for i in range(len(imgs) - 2, -1, -1):
        if imgs[i + 1].stat().st_mtime - imgs[i].stat().st_mtime > gap_hours * 3600:
            break
        batch.insert(0, imgs[i])
    return batch


def guess_start_group():
    """从第一个「还是旧图」的姿势推断这批该从第几组开始。

    高清图高度都在 1000 上下，旧图只有 300 上下，一眼能分。
    这样明天生成完直接跑脚本就行，不用记上次做到第几组。
    """
    for g in sorted(GROUPS):
        for name in GROUPS[g]:
            p = HERE / f"{name}.png"
            if not p.exists():
                return g
            try:
                if Image.open(p).size[1] < MIN_H * 0.85:
                    return g
            except OSError:
                return g
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--group", type=int, help="只处理某一组")
    ap.add_argument("--src", help="指定单张源图（配合 --group）")
    ap.add_argument("--dir", default=str(DOWNLOADS), help="源图目录，默认 ~/Downloads")
    ap.add_argument("--all", action="store_true",
                    help="不做批次识别，目录下所有 ChatGPT_Image_*.png 都算（一般不需要）")
    ap.add_argument("--start-group", type=int,
                    help="第一张对应第几组（默认从第一个还是旧图的姿势自动推断）")
    args = ap.parse_args()

    if args.src:
        if not args.group:
            print("--src 必须配合 --group", file=sys.stderr)
            return 2
        pairs = [(args.group, Path(args.src))]
    else:
        # 按修改时间排序 = ChatGPT 的生成顺序 = 组顺序
        found = list(Path(args.dir).glob("ChatGPT_Image_*.png"))
        done = load_done()
        fresh = [p for p in found if p.name not in done]
        imgs = sorted(fresh, key=lambda p: p.stat().st_mtime) if args.all else latest_batch(fresh)
        if not imgs:
            print(f"没有未处理的新图（{args.dir} 下 {len(found)} 张，"
                  f"{len(done)} 张已切过）。新生成的图放进来再跑即可。")
            return 0
        start = args.start_group or guess_start_group()
        import datetime as _dt
        span = [_dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
                for p in (imgs[0], imgs[-1])]
        print(f"新图 {len(imgs)} 张（{span[0]} → {span[1]}）· 目录共 {len(found)} 张"
              f"（{len(done)} 张已切过，跳过）· 从组 {start} 开始\n")
        pairs = [(i, p) for i, p in enumerate(imgs, start)]

    print(f"{'[dry-run] ' if args.dry_run else ''}共 {len(pairs)} 张源图\n")
    allok = True
    for g, src in pairs:
        names = GROUPS.get(g)
        if not names:
            print(f"⛔ 组 {g} 不在映射表里，跳过 {src.name}")
            continue
        print(f"组 {g} ← {src.name}")
        allok &= split_one(src, names, args.dry_run)
        print()
    if not args.dry_run:
        mark_done([p for _, p in pairs])
        print("完成。旧图已备份到 _原图备份/，本批源图已记入 .split_state.json（下次自动跳过）")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
