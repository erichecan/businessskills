#!/usr/bin/env python3
"""审核标准校准 —— 拿发出去之后的真实数据，反过来问「审核那七个维度到底准不准」。

要回答的问题只有一个：**审核给的分，和这篇真实表现，是不是同一个方向？**
如果某个维度打得高的稿反而扑街，那条判据就是错的，该改的是 skill 不是稿子。

做法：
  审核记录.csv（独立审核行：总分 + 选题/标题/首图/开头/正文/可信度/CTA 七个维度分）
    → 成稿文件 → parse_draft 取发布标题
    → 发布数据.csv（后台抓回的真实观看/点赞/收藏/评论/分享/搜索来源占比）
  两边按标题对上，算每个维度分与真实表现的**秩相关**（Spearman）。

⛔ 三条不可妥协的纪律，否则这个脚本会制造出比没有更糟的东西：

 1. **样本不够就不出结论。** 相关系数在 n=3 时几乎必然出现 |ρ|>0.8 的巧合。
    低于 MIN_SAMPLE 一律只报「还差几篇」，绝不给系数。
 2. **只用发满 MIN_DAYS 天的笔记。** 发布当天的数据是冷启动噪声，
    拿它反推审核标准等于用抛硬币的结果去改考试大纲。
 3. **本脚本不改任何 skill 文件。** 它只出证据和建议，改不改由人决定。
    评分标准一旦能被脚本自动改写，就没有任何东西能拦住它慢慢漂移到一个自洽但错误的口径上。

用法：
  python3 calibrate_audit.py              # 出报告，写 xhs/素材库/审核校准报告.md
  python3 calibrate_audit.py --stdout      # 只打印
  python3 calibrate_audit.py --min-days 3  # 放宽天数门槛（会在报告里标注口径已放宽）
"""
import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
PUB_DATA = SUCAI / "发布数据.csv"
REPORT = SUCAI / "审核校准报告.md"
sys.path.insert(0, str(REPO / "scripts" / "case-entry"))

DIMS = ["选题", "标题", "首图", "开头", "正文", "可信度", "CTA"]
# 搜索流的主指标就是搜索来源占比（见 eric-xhs-audit 决策 4），放在第一位。
# 观看/点赞一并算，但它们受账号体量和时段污染，只作参照。
OUTCOMES = ["搜索来源占比", "观看", "点赞", "收藏", "评论"]
MIN_DAYS = 7
MIN_SAMPLE = 8          # 低于这个数不出系数。7 个维度 × 5 个指标 = 35 个数，n<8 时纯属噪声


def read_csv(p):
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(v):
    """'12.3%' → 12.3；'1,234' → 1234；取不出数就 None（不当 0，0 是个真实值）。"""
    s = str(v or "").strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def latest_stats():
    """同一篇笔记会被抓多次，只留发布天数最大的那一行。"""
    best = {}
    for r in read_csv(PUB_DATA):
        t = (r.get("标题") or "").strip()
        d = num(r.get("发布天数"))
        if not t or d is None:
            continue
        if t not in best or d > num(best[t].get("发布天数")):
            best[t] = r
    return best


def audited_drafts():
    """每篇稿只取最后一条独立审核。人工放行不算 —— 那是人推翻审核，不是审核的判断。"""
    out = {}
    for r in read_csv(AUDIT_LOG):
        if (r.get("审核方") or "").strip() != "独立审核":
            continue
        out[(r.get("成稿文件") or "").strip()] = r
    return out


def draft_title(name):
    from case_entry import parse_draft
    for p in (SUCAI / name, SUCAI / "归档稿" / name):
        if p.exists():
            return (parse_draft(p.read_text(encoding="utf-8")).get("title") or "").strip()
    return ""


def spearman(xs, ys):
    """秩相关。用秩而非原值：观看量是长尾分布，Pearson 会被一个爆款完全带跑。"""
    n = len(xs)
    if n < 3:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:                      # 并列取平均秩，否则同分样本会被人为拉开
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if dx == 0 or dy == 0 else cov / (dx * dy)


def build_pairs(min_days):
    stats, audits = latest_stats(), audited_drafts()
    paired, pending = [], []
    for name, a in audits.items():
        title = draft_title(name)
        if not title or title not in stats:
            continue
        s = stats[title]
        days = num(s.get("发布天数")) or 0
        # ⛔ 笔记标题的键不能叫「标题」—— DIMS 里也有个维度叫「标题」，
        # 同名会被维度分覆盖掉，报告里就成了「已发 3 天」旁边跟着一个分数。
        row = {"成稿": name, "发布标题": title, "天数": days,
               "总分": num(a.get("总分")),
               **{d: num(a.get(d)) for d in DIMS},
               **{o: num(s.get(o)) for o in OUTCOMES}}
        (paired if days >= min_days else pending).append(row)
    return paired, pending


def render(paired, pending, min_days, relaxed):
    L = [f"# 审核标准校准报告 · {date.today().isoformat()}", ""]
    if relaxed:
        L += [f"> ⚠️ 本次口径已放宽到「发布满 {min_days} 天」（默认 {MIN_DAYS} 天）。"
              f"天数越短，冷启动噪声占比越大，结论请按此折扣看。", ""]

    L += [f"**可用样本：{len(paired)} 篇**（既有独立审核、又有发满 {min_days} 天的真实数据）", ""]

    if pending:
        L += [f"还在等的 {len(pending)} 篇（已发布但天数不够）：", ""]
        for r in sorted(pending, key=lambda x: -x["天数"]):
            L.append(f"- {r['发布标题'][:28]} — 已发 {int(r['天数'])} 天，"
                     f"还差 {max(0, min_days - int(r['天数']))} 天 · 审核 {r['总分']} 分")
        L.append("")

    if len(paired) < MIN_SAMPLE:
        L += ["## ⛔ 样本不足，本次不给任何相关系数", "",
              f"现有 {len(paired)} 篇，门槛 {MIN_SAMPLE} 篇，还差 **{MIN_SAMPLE - len(paired)} 篇**。", "",
              "为什么不凑合着算：7 个维度 × 5 个指标 = 35 个系数，样本个位数时",
              "必然会蹦出几个 |ρ|>0.8 的「强相关」，那是巧合不是规律。",
              "拿它去改审核标准，等于用噪声重写考试大纲 —— 比不改更糟，",
              "因为改完之后所有稿都会朝那个错方向优化，而且没人会怀疑它。", "",
              "**在此之前，审核标准的唯一依据仍是采集数据**"
              "（`docs/20260804-标题真实规律-采集数据实证.md`，330 条搜索位笔记），",
              "那份样本量够，且已写进 eric-xhs-audit 的维度 2。", ""]
        return "\n".join(L).rstrip() + "\n"

    L += ["## 各维度分 vs 真实表现（Spearman 秩相关）", "",
          "系数为正 = 这个维度打得高的稿，真实表现也好，判据有效。",
          "系数接近 0 = 这条判据和结果无关，白扣分。",
          "**系数为负 = 判据方向反了，越符合标准表现越差，必须改。**", "",
          "| 维度 | " + " | ".join(OUTCOMES) + " |",
          "|---|" + "---|" * len(OUTCOMES)]
    flags = []
    for d in DIMS + ["总分"]:
        cells = []
        for o in OUTCOMES:
            xs = [r[d] for r in paired if r[d] is not None and r[o] is not None]
            ys = [r[o] for r in paired if r[d] is not None and r[o] is not None]
            rho = spearman(xs, ys) if len(xs) >= 3 else None
            cells.append("—" if rho is None else f"{rho:+.2f}")
            if rho is not None and o == OUTCOMES[0] and rho < -0.3:
                flags.append((d, rho))
        L.append(f"| {d} | " + " | ".join(cells) + " |")
    L.append("")

    if flags:
        L += ["## ⚠️ 方向可疑的维度（与主指标负相关）", ""]
        for d, rho in flags:
            L.append(f"- **{d}**：与「{OUTCOMES[0]}」秩相关 {rho:+.2f} —— "
                     f"这条判据打得越高，搜索进入反而越少。去 `skills/eric-xhs-audit/SKILL.md` "
                     f"看维度「{d}」写了什么，对照实际稿子确认是不是判据本身错了。")
        L.append("")
    else:
        L += ["## 没有方向反了的维度", "",
              "所有维度与主指标的相关性都不为显著负，暂无需要推翻的判据。", ""]

    L += ["## 逐篇明细", "",
          "| 标题 | 天数 | 总分 | " + " | ".join(OUTCOMES) + " |",
          "|---|---|---|" + "---|" * len(OUTCOMES)]
    for r in sorted(paired, key=lambda x: -(x["总分"] or 0)):
        L.append(f"| {r['发布标题'][:24]} | {int(r['天数'])} | {r['总分']} | "
                 + " | ".join("—" if r[o] is None else f"{r[o]:g}" for o in OUTCOMES) + " |")
    L += ["", "---", "",
          "⛔ 本报告不改任何 skill 文件。要不要按它改审核标准，由人决定。"]
    return "\n".join(L).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    args = ap.parse_args()

    paired, pending = build_pairs(args.min_days)
    text = render(paired, pending, args.min_days, args.min_days != MIN_DAYS)
    print(text)
    if not args.stdout:
        REPORT.write_text(text, encoding="utf-8")
        print(f"→ 已写入 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
