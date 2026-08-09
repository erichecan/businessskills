#!/usr/bin/env python3
"""搜索位空缺探测器 · 第 3 层：结构化回填。

读第 1 层的 probe_*.json 与第 2 层的 probe_*.result.json，写回：
  词库.csv        — 竞争密度 / 状态 / 场景类型 / 意图强度（只改这四列）
  评论区原话.csv  — 追加筛选后的原话
  探测日志.csv    — 每轮一行

设计依据：docs/20260802-eric-xhs-probe-搜索位空缺探测器实施方案.md
实施修正（2026-08-02）：不写 运行日志.csv。那个文件由采集任务独占，
health_check.py 拿它的末行日期比对 xlsx 判断采集是否断流，插入探测行会造成误告警。

用法：
  python3 backfill.py --date 20260802
  python3 backfill.py --date 20260802 --dry-run
"""
import argparse
import csv
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
QUOTES = SUCAI / "评论区原话.csv"
PROBE_LOG = SUCAI / "探测日志.csv"
OUT_DIR = SUCAI / "探测原始"
BAK_DIR = SUCAI / ".bak"
KEEP_BACKUPS = 10

# 「竞争密度」只装探测数据，「人工初判」装主观判断——两者混在一列曾导致
# 「不想做」被记成「竞争激烈」，P1 校准因此判定不通过（2026-08-02 拆分）
CIKU_COLS = ["关键词", "场景域", "场景类型", "意图强度", "竞争密度", "人工初判",
             "关联案例ID", "状态", "发布日", "笔记链接", "搜索来源占比", "备注"]
QUOTE_COLS = ["日期", "来源链接", "用户原话", "暴露的处境", "候选词"]
PROBE_LOG_COLS = ["日期", "轮次", "探测词数", "成功数", "新增原话数", "新增候选词数",
                  "小红书状态", "密度分布", "告警", "备注"]

DISPOSITION_TO_STATUS = {"做": "已验证", "缓": "候选", "放弃": "放弃"}
# 已成稿/已发布的词不被自动回填降级。「已使用」是旧值，保留兼容
PROTECTED_STATUS = {"已使用", "已成稿", "已发布"}
PLACEHOLDER = {"", "待探测", "待归类", "待验证"}


def backup(path):
    BAK_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = BAK_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, dst)
    olds = sorted(BAK_DIR.glob(f"{path.stem}_*{path.suffix}"))
    for old in olds[:-KEEP_BACKUPS]:
        old.unlink()
    return dst


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, cols):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def load_pairs(day):
    """(probe.json, result.json) 配对。result 缺失说明第 2 层还没跑。"""
    pairs = []
    for p in sorted(OUT_DIR.glob(f"probe_{day}_*.json")):
        if p.name.endswith(".result.json"):
            continue
        r = p.with_suffix("").with_suffix("")
        r = OUT_DIR / (p.stem + ".result.json")
        pairs.append((p, r if r.exists() else None))
    return pairs


def backfill_ciku(results, dry, quotes_only=False):
    """quotes_only：阈值校准未通过时用——只收新候选词，不写竞争密度/状态。"""
    rows = read_csv(CIKU)
    index = {r["关键词"].strip(): r for r in rows}
    changed, added = [], []

    for res in [] if quotes_only else results:
        kw = res["keyword"].strip()
        row = index.get(kw)
        if row is None:
            continue

        if row.get("状态", "").strip() in PROTECTED_STATUS:
            changed.append((kw, "跳过（已使用，受保护）"))
            continue

        before = (row.get("竞争密度", ""), row.get("状态", ""))
        if res.get("density_echo"):
            row["竞争密度"] = res["density_echo"]
        status = DISPOSITION_TO_STATUS.get(res.get("disposition", ""))
        if status:
            row["状态"] = status
        if row.get("场景类型", "").strip() in PLACEHOLDER and res.get("scene_type"):
            row["场景类型"] = res["scene_type"]
        if row.get("意图强度", "").strip() in PLACEHOLDER and res.get("intent"):
            row["意图强度"] = res["intent"]
        after = (row["竞争密度"], row["状态"])
        if before != after:
            changed.append((kw, f"{before[0]}/{before[1]} → {after[0]}/{after[1]}"))

    for res in results:
        for q in res.get("quotes", []):
            new_kw = (q.get("候选词") or "").strip()
            if not new_kw or new_kw in index:
                continue
            row = {c: "" for c in CIKU_COLS}
            # 状态用「候选」不用「待验证」：2026-08-03 Eric 定 —— 不要「待验证」这个中间态，
            # 成稿后发布让市场检验。状态机收敛为
            #   候选 →[probe+auto_analyze]→ 已验证(做)/候选(缓)/放弃 →[refine_loop]→ 已成稿 → 已发布
            # 「待验证」曾经的含义是「探测过但等人工点头」，那一步已由 auto_analyze.py 接管。
            row.update({"关键词": new_kw, "场景域": "待归类", "场景类型": "待归类",
                        "意图强度": "待探测", "竞争密度": "待探测", "状态": "候选"})
            rows.append(row)
            index[new_kw] = row
            added.append(new_kw)

    if not dry and (changed or added):
        backup(CIKU)
        write_csv(CIKU, rows, CIKU_COLS)
    return changed, added


def backfill_quotes(results, dry):
    rows = read_csv(QUOTES)
    seen = {(r.get("来源链接", "").strip(), r.get("用户原话", "").strip()[:20]) for r in rows}
    new = []
    for res in results:
        for q in res.get("quotes", []):
            text = (q.get("用户原话") or "").strip()
            link = (q.get("来源链接") or "").strip()
            if not text:
                continue
            key = (link, text[:20])
            if key in seen:
                continue
            seen.add(key)
            new.append({c: (q.get(c) or "").strip() for c in QUOTE_COLS})

    if new and not dry:
        if QUOTES.exists():
            backup(QUOTES)
        write_csv(QUOTES, rows + new, QUOTE_COLS)
    return new


def write_probe_log(day, probes, results, quotes_added, words_added, dry):
    total = len(probes)
    ok = sum(1 for p in probes if p.get("completeness") == "full")
    captcha = any(p.get("_error") == "captcha_triggered" for p in probes)
    dist = {}
    for p in probes:
        v = p.get("density", {}).get("verdict", "待探测")
        dist[v] = dist.get(v, 0) + 1

    row = {
        "日期": f"{day[:4]}-{day[4:6]}-{day[6:]}",
        "轮次": f"probe-{datetime.now().strftime('%H%M')}",
        "探测词数": total,
        "成功数": ok,
        "新增原话数": len(quotes_added),
        "新增候选词数": len(words_added),
        "小红书状态": "触发安全验证" if captcha else "正常",
        "密度分布": " ".join(f"{k}:{v}" for k, v in sorted(dist.items())),
        "告警": "是" if captcha or ok == 0 else "否",
        "备注": f"已分析 {len(results)}/{total}" + ("；剩余词待 --resume" if captcha else ""),
    }
    if not dry:
        exists = PROBE_LOG.exists()
        with PROBE_LOG.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PROBE_LOG_COLS)
            if not exists:
                w.writeheader()
            w.writerow(row)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat().replace("-", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quotes-only", action="store_true",
                    help="只回填原话与新候选词，不写竞争密度/状态（阈值校准未通过时用）")
    args = ap.parse_args()

    pairs = load_pairs(args.date)
    if not pairs:
        print(f"{args.date} 没有探测结果", file=sys.stderr)
        return 1

    probes, results, missing = [], [], []
    for pj, rj in pairs:
        probes.append(json.loads(pj.read_text(encoding="utf-8")))
        if rj:
            results.append(json.loads(rj.read_text(encoding="utf-8")))
        else:
            missing.append(pj.stem)

    if missing:
        print(f"以下词缺 .result.json（第 2 层未分析），本次跳过其回填：", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)

    changed, added = backfill_ciku(results, args.dry_run, args.quotes_only)
    quotes = backfill_quotes(results, args.dry_run)
    log = write_probe_log(args.date, probes, results, quotes, added, args.dry_run)

    tag = "[dry-run] " if args.dry_run else ""
    if args.quotes_only:
        print(f"{tag}quotes-only 模式：竞争密度与状态未写入")
    print(f"{tag}词库更新 {len(changed)} 行：")
    for kw, how in changed:
        print(f"    {kw}: {how}")
    print(f"{tag}词库新增 {len(added)} 个候选词" + (f"：{', '.join(added)}" if added else ""))
    print(f"{tag}原话新增 {len(quotes)} 条")
    print(f"{tag}探测日志：{log['探测词数']}词/成功{log['成功数']} 密度[{log['密度分布']}] "
          f"小红书={log['小红书状态']} 告警={log['告警']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
