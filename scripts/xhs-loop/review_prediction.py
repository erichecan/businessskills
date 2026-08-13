#!/usr/bin/env python3
"""预测复盘 —— 发布满 7 天后拿真实数据对账，自己 review 为什么没预测准。

Eric 2026-08-03 的要求原话：「拿到数据回填后的审查，你要自己去 review 一下，
当时为什么没预测准，如何能让你自己预测更准一些，包括评论，转发，收藏数据等等。」

对账链路：预测记录.成稿文件 → 关键词 → 词库.笔记链接 → noteId → 发布数据.csv 取发布≥7天的行。

机械部分（命中/高估/低估、偏差倍数）由代码算，不让模型自报；
归因部分（为什么错、怎么改系数）交独立 claude -p 进程做——它拿到的是算好的偏差表，
不是原始数据，避免模型一边算一边给自己找补。

用法：
  python3 review_prediction.py            # 对账所有够 7 天的，写入 预测复盘.md
  python3 review_prediction.py --list     # 只看哪些够/不够 7 天
  python3 review_prediction.py --no-llm   # 只出偏差表，不做归因
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_cli  # noqa: E402
from claude_limits import WEEKLY, classify_limit, limit_banner  # noqa: E402
from headless_cli import HEADLESS_FLAGS, ensure_cwd  # noqa: E402

BARE_CWD = ensure_cwd()
REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
PRED = SUCAI / "预测记录.csv"
CIKU = SUCAI / "词库.csv"
STATS = SUCAI / "发布数据.csv"
REVIEW = SUCAI / "预测复盘.md"
# 复盘的结构化副本。md 是给人读的（归因写成三段散文），这份是给 predict.py 读的。
# ⛔ 2026-08-13 加。在此之前复盘结论**只**进 md，而全仓库没有第二个脚本读那个 md ——
# 数据跑到复盘就是死路：模型明确写出「VIEWS_BASE['低'] 该按 median_likes 拆两档」，
# 却没有任何通道把这句话送回 predict.py，只能靠人读了再手改。
CALIB = SUCAI / "预测校准.csv"
CALIB_COLS = ["复盘日", "成稿文件", "关键词", "密度", "前排中位赞", "审核分",
              "指标", "预测低", "预测高", "实际", "判定", "倍数"]
DOC = REPO / "docs" / "20260803-小红书数据预测调研.md"
CLAUDE = Path.home() / ".local/bin/claude"

MIN_DAYS = 7
METRICS = ["观看", "点赞", "收藏", "评论", "转发", "CES"]


def read_rows(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def note_id_of(keyword):
    for r in read_rows(CIKU):
        if (r.get("关键词") or "").strip() == keyword:
            link = (r.get("笔记链接") or "").strip()
            m = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", link)
            return m.group(1) if m else None
    return None


def actual_of(note_id):
    """取该笔记发布天数 ≥7 的最新一行。不足 7 天的不参与对账——
    搜索长尾要几天才爬上来，拿第 1 天的数据去对 7 天的预测，
    结论必然是「全部高估」，那是对账方法错了不是模型错了。"""
    best = None
    for r in read_rows(STATS):
        if (r.get("笔记ID") or "").strip() != note_id:
            continue
        try:
            days = int((r.get("发布天数") or "0").strip())
        except ValueError:
            continue
        if days >= MIN_DAYS and (best is None or days > best[0]):
            best = (days, r)
    return best


def verdict(lo, hi, act):
    if act is None:
        return "无数据", 0.0
    if lo <= act <= hi:
        return "命中", 1.0
    if act > hi:
        return "低估", round(act / hi, 2) if hi else 0.0
    return "高估", round(act / lo, 2) if lo else 0.0


def compare(pred_row, days, act_row):
    rows = []
    for m in METRICS:
        lo = int(pred_row.get(f"{m}_低") or 0)
        hi = int(pred_row.get(f"{m}_高") or 0)
        if m == "CES":
            a = (int(act_row.get("点赞") or 0) + int(act_row.get("收藏") or 0)
                 + int(act_row.get("评论") or 0) * 4 + int(act_row.get("分享") or 0) * 4)
        else:
            a = int(act_row.get({"转发": "分享"}.get(m, m)) or 0)
        v, ratio = verdict(lo, hi, a)
        rows.append({"指标": m, "预测低": lo, "预测高": hi, "实际": a, "判定": v, "倍数": ratio})
    return rows


def render_table(rows):
    out = ["| 指标 | 预测区间 | 实际 | 判定 |", "|---|---|---|---|"]
    for r in rows:
        mark = {"命中": "✅", "低估": "⬆️", "高估": "⬇️"}.get(r["判定"], "—")
        extra = "" if r["判定"] == "命中" else f"（{r['倍数']}×）"
        out.append(f"| {r['指标']} | {r['预测低']}–{r['预测高']} | {r['实际']} | {mark} {r['判定']}{extra} |")
    return "\n".join(out)


_BASIS_MED = re.compile(r"前排中位\s*([\d.]+)\s*赞")
_BASIS_DENSITY = re.compile(r"密度(\S?)\(")


def append_calibration(pred_row, rows):
    """把这次对账的每个指标写一行进 预测校准.csv，供 predict.py 读。

    为什么要单独存一份而不是让 predict 去 parse md：归因是三段自然语言，
    parse 它等于把「模型写了什么」当判据；而校准要的是**代码算出来的偏差数字**，
    这两者必须分开 —— 前者会变，后者是事实。
    """
    basis = pred_row.get("依据", "") or ""
    med = (_BASIS_MED.search(basis) or [None, ""])[1]
    density = (_BASIS_DENSITY.search(basis) or [None, ""])[1]
    today = date.today().isoformat()
    new = not CALIB.exists()
    with CALIB.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CALIB_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({
                "复盘日": today, "成稿文件": pred_row.get("成稿文件", ""),
                "关键词": pred_row.get("关键词", ""), "密度": density,
                "前排中位赞": med, "审核分": pred_row.get("审核分", ""),
                "指标": r["指标"], "预测低": r["预测低"], "预测高": r["预测高"],
                "实际": r["实际"], "判定": r["判定"], "倍数": r["倍数"],
            })


def _run_attribution(prompt: str, tag: str) -> str:
    """跑归因：先 Gemini（免费），拿不到再退 claude -p。

    ⛔ 2026-08-13 之前这里是**裸** `subprocess.run([CLAUDE, "-p", prompt])`：
      · 没有 HEADLESS_FLAGS —— 每次都把全套工具定义注进去，白背约 7k token，
        而这个任务给的是算好的偏差表，模型一个工具都不需要
      · 没有 claude_limits —— 撞额度时 claude 把提示打到 stdout 且 returncode=0，
        于是那句「You've hit your weekly limit」会被当成归因结论**原样写进 预测复盘.md**
    一次运行要跑十几篇，这两条叠起来很贵也很脏。
    """
    if os.environ.get("XHS_ENGINE", "gemini").lower() != "claude" and gemini_cli.available():
        try:
            out = gemini_cli.run(prompt, temperature=0.3)
            if out:
                print(f"   · {tag} 由 Gemini 归因（未消耗 Claude 额度）", flush=True)
                return out
        except gemini_cli.QuotaExhausted as e:
            print(f"   ⏬ Gemini 额度已满，回退 Claude：{e}", flush=True)
        except Exception as e:                        # noqa: BLE001
            print(f"   ⏬ Gemini 归因失败，回退 Claude：{e}", flush=True)

    waited = 0
    while True:
        r = subprocess.run([str(CLAUDE), *HEADLESS_FLAGS, "-p", prompt],
                           cwd=str(BARE_CWD), capture_output=True, text=True, timeout=600)
        out = (r.stdout or "").strip()
        kind = classify_limit(out, (r.stderr or "")[:2000])
        if not kind:
            return out
        if kind == WEEKLY:
            print(limit_banner(kind, tag), flush=True)
            return ""            # 宁可留空，也不要把额度提示当成归因结论写进复盘
        if waited >= 2 * 3600:
            print(f"   ⛔ {tag} 撞额度累计等待 {waited//60} 分钟，停手", flush=True)
            return ""
        waited += 30 * 60
        print(f"   ⏳ {tag} 撞额度（{kind}），等 30 分钟重试", flush=True)
        time.sleep(30 * 60)


def attribute(pred_row, days, table, prior_reviews):
    """偏差归因交独立进程。喂算好的偏差表 + 模型假设文档，让它说清哪条假设被证伪了。"""
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else "（调研文档缺失）"
    prompt = f"""你在复盘一次小红书笔记数据预测。偏差表是代码算好的，你不要重算。

【预测模型的假设与系数】
{doc}

【这篇的预测依据】
{pred_row.get('依据', '')}
口径 {pred_row.get('口径')} · 关键词「{pred_row.get('关键词')}」· 发布满 {days} 天

【偏差对账表（代码所算，以此为准）】
{table}

【此前的复盘结论（避免重复同样的归因）】
{prior_reviews[-3000:] if prior_reviews else '（首次复盘）'}

回答三个问题，每个不超过 4 句，必须具体到系数和数字，禁止「需要进一步观察」这类空话：
1. **哪条假设被证伪了** —— 指名调研文档第四节里的哪一条，用偏差表的数字作证。
2. **为什么没预测准** —— 区分三种原因：模型系数错 / 输入数据错（如密度未探测按中档估）/
   对账方法错（如搜索流量还没进来）。选一种并说明理由。
3. **具体怎么改** —— 给出要改的系数名和新取值，例如「VIEWS_BASE['低'] 从 250-600 改为 120-400」。
   样本不足以支撑改系数时就明说「本次不改，需再攒 N 篇」。

输出 markdown，不要标题，直接三段。"""
    return _run_attribution(prompt, pred_row.get("成稿文件", "?"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    preds = read_rows(PRED)
    if not preds:
        print("预测记录.csv 为空，先跑 predict.py")
        return 0

    done = set(re.findall(r"^## .*?· (成稿_.*?\.md)", REVIEW.read_text(encoding="utf-8"), re.M)) \
        if REVIEW.exists() else set()

    ready, waiting = [], []
    for p in preds:
        nid = note_id_of((p.get("关键词") or "").strip())
        hit = actual_of(nid) if nid else None
        (ready if hit else waiting).append((p, nid, hit))

    if args.list:
        print(f"可复盘 {len([x for x in ready if x[0]['成稿文件'] not in done])} 篇"
              f"（已复盘 {len(done)}）· 等数据 {len(waiting)} 篇")
        for p, nid, hit in ready:
            flag = "已复盘" if p["成稿文件"] in done else "待复盘"
            print(f"  [{flag}] {p['成稿文件']}（发布 {hit[0]} 天）")
        for p, nid, _ in waiting:
            why = "无笔记链接" if not nid else f"不足 {MIN_DAYS} 天或无回填数据"
            print(f"  [等待] {p['成稿文件']} — {why}")
        return 0

    # ⛔ 2026-08-13 修重复复盘。同一篇稿在 预测记录.csv 里会有**多条**预测
    # （每次返工过线都写一条，审核分不同），而 done 只反映**文件里已有的**记录，
    # 同一次运行内拦不住。实测 14 条复盘里 5 条是重复的：
    # 08-08_汇报被打断 ×4、08-11_汇报被打断 ×3，每条都真调了一次模型。
    #
    # 危害不止是浪费额度：复盘自己立的规矩是「累计 ≥5 篇有 7 天数据才允许改系数」，
    # 重复记录会让样本数虚高，可能拿着 1 篇的证据去改本该等 5 篇的系数。
    #
    # 同一篇取**最后一条**预测：实际数据对应的是最终发布出去的那一版。
    #
    # ⛔ 去重键是 **noteId 不是成稿文件名**。同一个关键词常有好几篇稿
    # （反复重写「汇报被领导打断怎么接」写了 6 篇），但只有一篇真发布出去，
    # 于是这 6 篇会全部对账到**同一份实际数据**、给出同一个偏差倍数。
    # 按文件名去重的话，1 个真实样本会被记成 6 个 —— 「累计 ≥5 篇才改系数」
    # 这条闸门当场失守：看着够 5 篇了，其实只有 1 个笔记的证据。
    # 实测回填 9 篇复盘，观看指标只有 2 个独立值（0.24× 与 0.45×）。
    seen, todo = set(), []
    for p, nid, hit in reversed(ready):
        key = nid or p["成稿文件"]          # 没有 noteId 时退回文件名，至少别漏
        if p["成稿文件"] in done or key in seen:
            continue
        seen.add(key)
        todo.append((p, nid, hit))
    todo.reverse()
    if not todo:
        print(f"没有待复盘的（可复盘 {len(ready)} 篇均已复盘，{len(waiting)} 篇还在等 {MIN_DAYS} 天数据）")
        return 0

    prior = REVIEW.read_text(encoding="utf-8") if REVIEW.exists() else ""
    blocks = []
    for p, nid, (days, act) in todo:
        rows = compare(p, days, act)
        table = render_table(rows)
        hits = sum(1 for r in rows if r["判定"] == "命中")
        print(f"\n【{p['成稿文件']}】发布 {days} 天 · {hits}/{len(METRICS)} 命中")
        print(table)
        append_calibration(p, rows)
        note = "" if args.no_llm else attribute(p, days, table, prior)
        blocks.append(f"""
## {date.today().isoformat()} · {p['成稿文件']}

关键词「{p['关键词']}」· {p['口径']} · 发布满 {days} 天 · 命中 {hits}/{len(METRICS)}
预测依据：{p.get('依据','')}

{table}

{note}
""")
    if blocks:
        new = not REVIEW.exists()
        with REVIEW.open("a", encoding="utf-8") as f:
            if new:
                f.write("# 预测复盘记录\n\n"
                        "> 由 scripts/xhs-loop/review_prediction.py 写入。偏差表是代码算的，归因是独立进程做的。\n"
                        "> 累计 ≥5 篇有 7 天数据后，才允许把先验系数改成拟合值。\n")
            f.write("\n".join(blocks))
        print(f"\n✅ 已写入 {REVIEW.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
