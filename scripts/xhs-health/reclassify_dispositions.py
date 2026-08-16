#!/usr/bin/env python3
"""按 decide_disposition 重判 审核记录.csv 里所有独立审核行的「处置」列。

为什么要回头改历史行：处置列现在归代码所有（和「审核方」「口径」一样），
但 2026-08-05 之前那 45 行是模型自己填的，规则不一致 —— 其中 2 篇 86 分无红线的稿
被写成「待人工」，一直卡在发布闸门外；另有 28 篇 75-84 分的稿本该进「返工」档
让 loop 继续改，却因为落在「待人工」这个死档里再没人碰过。

⛔ 只改「处置」这一列。总分、红线、维度分是审核员的判断，代码无权改。
备注列追加一条改判说明，保留模型原来写的处置值，便于回溯。

用法：
  python3 reclassify_dispositions.py --dry-run   # 只看会改什么
  python3 reclassify_dispositions.py             # 真的改（先备份）
"""
import argparse
import csv
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from independent_audit import decide_disposition

REPO = Path(__file__).resolve().parents[2]
AUDIT_LOG = REPO / "xhs" / "素材库" / "审核记录.csv"
SUCAI = AUDIT_LOG.parent
DRAFT_CHECK = Path(__file__).parent / "draft_check.py"

_MECH_CACHE = {}


def mech_ok_of(name):
    """这篇稿现在过不过机械项。实跑 draft_check --file，一篇只跑一次。

    ⛔ 必须传给 decide_disposition —— 不传的话它按 mech_ok=True 算，
    会把「分数够但机械项不过」的稿判成「发布」，而闸门那边照样拦，
    稿子就掉进 2026-08-14 记过的那个夹缝：处置写着发布，却永远发不出去。
    2026-08-16 起这一档有专门的去处（机修），更不能判错。
    """
    if name in _MECH_CACHE:
        return _MECH_CACHE[name]
    if not name:
        return True
    r = subprocess.run([sys.executable, str(DRAFT_CHECK), "--file", name,
                        "--lane", "搜索流"], capture_output=True, text=True)
    # 退出码 2 = 文件不在（素材库和归档稿都没有）。无从判定时按「通过」处理，
    # 免得把一批查无此文的历史行全推进「机修」这个需要动手的档。
    _MECH_CACHE[name] = r.returncode != 1
    return _MECH_CACHE[name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with AUDIT_LOG.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        cols = list(rows[0].keys())

    changed = []
    for r in rows:
        if (r.get("审核方") or "").strip() != "独立审核":
            continue          # 自评行不动：它本来就没有处置权（D7）
        old = (r.get("处置") or "").strip()
        new = decide_disposition(r.get("总分"), r.get("红线"),
                                 mech_ok=mech_ok_of((r.get("成稿文件") or "").strip()))
        if old == new:
            continue
        r["处置"] = new
        note = (r.get("备注") or "").rstrip("；")
        r["备注"] = f"{note}；{date.today().isoformat()} 按分档规则由「{old}」改判「{new}」"
        changed.append((r.get("成稿文件"), r.get("总分"), r.get("红线") or "无", old, new))

    print(f"{'分':>4} {'红线':<16} 改判            成稿")
    for f, s, rl, o, n in sorted(changed, key=lambda x: -int(x[1] or 0)):
        star = " ★" if n == "发布" else "  "
        print(f"{s:>4} {rl[:14]:<16} {o}→{n}{star}  {f}")
    print(f"\n共 {len(changed)} 行会改（总 {len(rows)} 行）")

    if args.dry_run:
        print("[dry-run] 未写入")
        return 0

    bak = AUDIT_LOG.with_suffix(f".csv.bak-{date.today().isoformat()}")
    shutil.copy2(AUDIT_LOG, bak)
    tmp = AUDIT_LOG.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    tmp.replace(AUDIT_LOG)
    print(f"✅ 已写入（原文件备份到 {bak.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
