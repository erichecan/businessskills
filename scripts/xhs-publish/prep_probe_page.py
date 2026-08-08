#!/usr/bin/env python3
"""给 click_probe.mjs 备一个可探测的发布页：预填一篇 + 打开定时开关，然后停手。

为什么单独一个脚本：click_probe 要探测的是「日历面板怎么才点得开」，
而日历只在**定时开关打开之后**才存在。手工准备这个前置状态要跑 auto_publish、
再在页面上点两下，探测就变成了「先干 5 分钟活才能开始」——那它就不会被跑第二次。

与 auto_publish 的区别：**不写发布日志、不碰词库、不走闸门**。
这是纯探测用的一次性页面，不该在账上留下任何「今天发过」的痕迹。

用法：
  python3 prep_probe_page.py                 # 自动挑一篇有成品图的稿
  python3 prep_probe_page.py --name 成稿_x.md
输出：最后一行是 tid=<targetId>，给 click_probe.mjs 用
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
sys.path.insert(0, str(REPO / "scripts" / "case-entry"))
sys.path.insert(0, str(Path(__file__).parent))


def pick_draft():
    """挑一篇成品图齐全的稿。探测只需要页面有内容，不需要它够格发布，
    所以这里刻意不走闸门 —— 否则闸门一严，探测就没得跑了。"""
    for f in sorted(SUCAI.glob("成稿_*.md"), reverse=True):
        stem = f.name.removeprefix("成稿_").removesuffix(".md")
        d = SUCAI / "成品图" / stem
        if d.is_dir() and list(d.glob("*.png")):
            return f.name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    name = args.name or pick_draft()
    if not name:
        print("⛔ 找不到任何带成品图的成稿", file=sys.stderr)
        return 1
    print(f"用这篇备页：{name}")

    from case_entry import prefill_xhs
    from auto_publish import open_sched_switch

    pre = prefill_xhs(name, archived=False)
    print(pre.get("log", ""))
    if not pre.get("ok"):
        print("⛔ 预填失败，探测无法进行", file=sys.stderr)
        return 1

    sw = open_sched_switch(pre["tid"])
    print(f"定时开关：{sw}")
    if "已打开" not in str(sw) and "已开" not in str(sw):
        print("⚠️ 定时开关没打开，日历区域可能不存在 —— 探测大概率会报「没有 .post-time-wrapper」",
              file=sys.stderr)
    print(f"tid={pre['tid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
