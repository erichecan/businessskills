#!/usr/bin/env python3
"""审核标准校准 —— 拿发出去之后的真实数据，反过来问「审核那七个维度到底准不准」。

要回答的问题只有一个：**审核给的分，和这篇真实表现，是不是同一个方向？**
如果某个维度打得高的稿反而扑街，那条判据就是错的，该改的是 skill 不是稿子。

做法：
  审核记录.csv（独立审核行：总分 + 选题/标题/首图/开头/正文/可信度/CTA 七个维度分）
    → 成稿文件 → 发布日志.csv 的「笔记链接」→ 笔记ID
    → 发布数据.csv（后台抓回的真实观看/点赞/收藏/评论/分享/搜索来源占比）
  两边按笔记ID对上，算每个维度分与真实表现的**秩相关**（Spearman）。

⛔ 2026-09-13 改：原来按「审核记录里存的标题」去 发布数据.csv 里找同名行——
实测 220 篇独立审核里只有 1 篇能对上号，因为标题在最后一次独立审核**之后**
经常又被机修/标题档改过一轮（改完不重审，见 refine_loop.mech_fix_one /
title_fix_one「未重审，沿用原XX分」那段），审核记录里存的是改之前的标题，
跟真正发布出去的标题对不上。发布日志.csv 的「笔记链接」是发布那一刻写的、
真实提交后的笔记 URL，不会因为之后改标题而变——改成从这里取笔记ID 再去
发布数据.csv 找，79/79 成稿文件全部精确对上，标题字符串匹配比不了。

⛔ 四条不可妥协的纪律，否则这个脚本会制造出比没有更糟的东西：

 1. **样本不够就不出结论。** 相关系数在 n=3 时几乎必然出现 |ρ|>0.8 的巧合。
    低于 MIN_SAMPLE 一律只报「还差几篇」，绝不给系数。
 2. **只用发满 MIN_DAYS 天的笔记。** 发布当天的数据是冷启动噪声，
    拿它反推审核标准等于用抛硬币的结果去改考试大纲。
 3. **本脚本不改任何 skill 文件。** 它只出证据和建议，改不改由人决定。
    评分标准一旦能被脚本自动改写，就没有任何东西能拦住它慢慢漂移到一个自洽但错误的口径上。
 4. **不同评分卡版本的维度分不能直接混在一起算相关系数。**
    ⛔ 2026-09-14 加，起因：`eric-xhs-audit` 的维度权重在 08-08/08-11/08-15
    改过三次（选题 20→32、标题 25→35、首图 20→10、开头 15→8、CTA 10→5、
    可信度 15→0 撤销），维度分的满分和分布本身随时间在跳变。把跨卡的分数
    混在一起算 Spearman，卡版本变化和「时间越晚账号越成熟」这类其它同期变化
    会一起被算进相关系数里——见得到的强相关（选题/标题 vs 评论 pooled +0.47）
    在按 `CURRENT_RUBRIC_SINCE` 拆开后于任一卡内单独算都掉到 |ρ|<0.15，
    是刻度错位造出来的相关，不是真信号。**headline 相关系数表只用同一张卡
    （当前卡）内的样本，跨卡样本只作历史参考、不进系数。**

用法：
  python3 calibrate_audit.py              # 出报告，写 xhs/素材库/审核校准报告.md
  python3 calibrate_audit.py --stdout      # 只打印
  python3 calibrate_audit.py --min-days 3  # 放宽天数门槛（会在报告里标注口径已放宽）
"""
import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
PUB_LOG = SUCAI / "发布日志.csv"
PUB_DATA = SUCAI / "发布数据.csv"
REPORT = SUCAI / "审核校准报告.md"

NOTE_ID_RE = re.compile(r"/explore/([0-9a-f]+)")

DIMS = ["选题", "标题", "首图", "开头", "正文", "可信度", "CTA"]

# ⛔ 2026-09-17 换主指标（Eric 定）：搜索来源占比 → **收藏率**，分享升为第二指标。
#
# 旧口径的依据是 07-31 决策 4「指标只盯搜索进入占比」。但那测的是**渠道效率**
# （被搜到了没有），不是**读者有没有被帮到**，而账号自己的数据已经把两者拆开了：
#   · 横向对比宽表 31 篇：合计 4716 观看 → 收藏 45 · 分享 5 · 评论 14，收藏率 0.95%
#   · 其中 **14 篇收藏=0，合计 1609 观看** —— 1609 个人点进来，没一个觉得值得存
#   · **收藏最高的 8 篇，搜索来源占比全是 0** —— 主指标和价值不只是弱相关，是脱钩
#
# 为什么是收藏和分享：读者被帮到时会留下的痕迹，按证明力排序是
#   观看（标题骗进来的，与内容无关）< 点赞（礼貌，很便宜）
#   < **收藏＝我以后要用它** < **分享＝我要拿它去帮另一个人**（读者替你做了利他）
# 09-16 评分卡已经换成「读者带走了什么」，这里跟上 —— 否则卡在优化 A、
# 校准在拟合 B，又是一次「写手看一张卡、审核看另一张卡」。
#
# 收藏率是**派生**指标：发布数据.csv 没有这一列，由 收藏/观看 现算（见 derive）。
# 搜索来源占比不删，降级为渠道诊断项留在表里 —— 它仍然回答「有没有被搜到」这个问题，
# 只是不再是"成功"的定义。
MAIN_OUTCOME = "收藏率"
OUTCOMES = ["收藏率", "分享", "收藏", "观看", "搜索来源占比", "评论"]
MIN_DAYS = 7
MIN_SAMPLE = 8          # 低于这个数不出系数。7 个维度 × 6 个指标 = 42 个数，n<8 时纯属噪声

# 当前评分卡生效日——来源 skills/eric-xhs-audit/SKILL.md「当前权重（2026-09-16 起）」，
# 这一行必须跟那份 skill 文件手动保持同步，它下次改权重时这里也要跟着改。
#
# ⛔ 2026-09-16 由 2026-08-15 推进到今天：评分卡按「优化读者带走什么」换了目标
# （搜索意图 32→35、正文 10→30、标题 35→20、首图 10→5、开头 8→5、CTA 5 不变）。
# 换权重意味着今天之后审出的分跟之前的**不可比** —— 同卡样本从今天起重新攒，
# 在攒够 MIN_SAMPLE 之前，本脚本会如实报「同卡样本不足，不给系数」，
# 那不是脚本坏了，是新尺子还没量够东西。旧卡的分留在逐篇明细里作历史参照。
CURRENT_RUBRIC_SINCE = "2026-09-16"


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


def latest_stats_by_note():
    """同一篇笔记会被抓多次，只留发布天数最大的那一行。按笔记ID索引——
    标题会被机修/标题档事后改掉，笔记ID不会，见本文件顶部 2026-09-13 那条注释。"""
    best = {}
    for r in read_csv(PUB_DATA):
        nid = (r.get("笔记ID") or "").strip()
        d = num(r.get("发布天数"))
        if not nid or d is None:
            continue
        if nid not in best or d > num(best[nid].get("发布天数")):
            best[nid] = r
    return best


def audited_drafts():
    """每篇稿只取最后一条独立审核。人工放行不算 —— 那是人推翻审核，不是审核的判断。"""
    out = {}
    for r in read_csv(AUDIT_LOG):
        if (r.get("审核方") or "").strip() != "独立审核":
            continue
        out[(r.get("成稿文件") or "").strip()] = r
    return out


def draft_to_note():
    """成稿文件 → 笔记ID，来源 发布日志.csv 的「笔记链接」列（只有真正发布成功的行才有值）。"""
    out = {}
    for r in read_csv(PUB_LOG):
        m = NOTE_ID_RE.search((r.get("笔记链接") or "").strip())
        if m:
            out[(r.get("成稿文件") or "").strip()] = m.group(1)
    return out


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
    stats, audits, d2n = latest_stats_by_note(), audited_drafts(), draft_to_note()
    paired, pending = [], []
    for name, a in audits.items():
        nid = d2n.get(name)
        if not nid or nid not in stats:
            continue
        s = stats[nid]
        title = (s.get("标题") or "").strip() or name
        days = num(s.get("发布天数")) or 0
        # ⛔ 笔记标题的键不能叫「标题」—— DIMS 里也有个维度叫「标题」，
        # 同名会被维度分覆盖掉，报告里就成了「已发 3 天」旁边跟着一个分数。
        audit_date = (a.get("日期") or "").strip()[:10]
        # 同卡 = 这篇稿子是在当前评分卡生效之后被审核出的分数，跟今天的权重
        # 口径一致。卡切换前审出的分数，维度满分/分布都不一样，不能直接拿来
        # 跟同期结果算相关系数（见文件头纪律 4）。
        same_card = bool(audit_date) and audit_date >= CURRENT_RUBRIC_SINCE
        # 收藏率是派生列（发布数据.csv 里没有），观看为 0 时留 None 不留 0 ——
        # 0 观看的篇收藏率是"算不出"，不是"0%"，当成 0 会把没曝光的稿混进最差档。
        views, saves = num(s.get("观看")), num(s.get("收藏"))
        derived = {"收藏率": (saves / views * 100) if views and saves is not None else None}
        row = {"成稿": name, "发布标题": title, "天数": days,
               "审核日": audit_date, "同卡": same_card,
               "总分": num(a.get("总分")),
               **{d: num(a.get(d)) for d in DIMS},
               **{o: (derived[o] if o in derived else num(s.get(o))) for o in OUTCOMES}}
        (paired if days >= min_days else pending).append(row)
    return paired, pending


def detail_table(paired) -> list:
    """逐篇明细。纯追溯用，不参与任何结论 —— 所以样本够不够都该出。"""
    L = ["## 逐篇明细（含跨卡样本，仅供追溯，卡口径见「卡」列）", "",
         "| 标题 | 卡 | 天数 | 总分 | " + " | ".join(OUTCOMES) + " |",
         "|---|---|---|---|" + "---|" * len(OUTCOMES)]
    for r in sorted(paired, key=lambda x: -(x["总分"] or 0)):
        card = "同卡" if r["同卡"] else "旧卡"
        # 收藏率是算出来的，不截位会印成 1.26582 —— 两位小数够了，多的是假精度。
        L.append(f"| {r['发布标题'][:24]} | {card} | {int(r['天数'])} | {r['总分']} | "
                 + " | ".join("—" if r[o] is None else
                              (f"{r[o]:.2f}%" if o == "收藏率" else f"{r[o]:g}")
                              for o in OUTCOMES) + " |")
    return L + ["", "---", "",
                "⛔ 本报告不改任何 skill 文件。要不要按它改审核标准，由人决定。"]


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

    same_card = [r for r in paired if r["同卡"]]
    old_card = [r for r in paired if not r["同卡"]]
    L += [f"**同卡样本（{CURRENT_RUBRIC_SINCE} 起，跟当前 eric-xhs-audit 权重口径一致）："
          f"{len(same_card)} 篇**；另有 {len(old_card)} 篇审核发生在改权重之前，"
          "维度满分/分布跟现在不一样，**只列作历史参照，不进下面的相关系数表**"
          "（原因见本文件头纪律 4）。", ""]

    if len(same_card) < MIN_SAMPLE:
        L += ["## ⛔ 同卡样本不足，本次不给任何相关系数", "",
              f"现有 {len(same_card)} 篇，门槛 {MIN_SAMPLE} 篇，还差 **{MIN_SAMPLE - len(same_card)} 篇**。", "",
              "为什么不凑合着算：7 个维度 × 6 个指标 = 42 个系数，样本个位数时",
              "必然会蹦出几个 |ρ|>0.8 的「强相关」，那是巧合不是规律。",
              "拿它去改审核标准，等于用噪声重写考试大纲 —— 比不改更糟，",
              "因为改完之后所有稿都会朝那个错方向优化，而且没人会怀疑它。", "",
              "**在此之前，审核标准的唯一依据仍是采集数据**"
              "（`docs/20260804-标题真实规律-采集数据实证.md`，330 条搜索位笔记），",
              "那份样本量够，且已写进 eric-xhs-audit 的维度 2。", ""]
        # ⛔ 2026-09-17 修：这里原来直接 return，于是**逐篇明细一并被跳过** ——
        # 而 CURRENT_RUBRIC_SINCE 那段注释白纸黑字写着「旧卡的分留在逐篇明细里作
        # 历史参照」。说的和做的不一致：每次换评分卡之后，在攒够 MIN_SAMPLE 之前
        # （按当前节奏 2-3 周），这份报告除了「还差 N 篇」什么都不显示，
        # 连已经回填好的真实数据都看不到。不给系数是对的（样本不够），
        # 不给**数据**没有理由 —— 明细本来就只是追溯用，不参与任何结论。
        L += detail_table(paired)
        return "\n".join(L).rstrip() + "\n"

    L += ["## 各维度分 vs 真实表现（Spearman 秩相关，仅同卡样本）", "",
          f"**主指标是「{MAIN_OUTCOME}」**（收藏＝我以后要用它），第二指标「分享」"
          "（＝我要拿它去帮另一个人）。搜索来源占比留在表里作**渠道诊断**，"
          "它回答的是「有没有被搜到」，不是「有没有帮到人」—— 2026-09-17 换锚，"
          "依据见本文件头 OUTCOMES 那段。", "",
          "系数为正 = 这个维度打得高的稿，真实表现也好，判据有效。",
          "系数接近 0 = 这条判据和结果无关，白扣分。",
          "**系数为负 = 判据方向反了，越符合标准表现越差，必须改。**", "",
          "| 维度 | " + " | ".join(OUTCOMES) + " |",
          "|---|" + "---|" * len(OUTCOMES)]
    flags = []
    for d in DIMS + ["总分"]:
        cells = []
        for o in OUTCOMES:
            xs = [r[d] for r in same_card if r[d] is not None and r[o] is not None]
            ys = [r[o] for r in same_card if r[d] is not None and r[o] is not None]
            rho = spearman(xs, ys) if len(xs) >= 3 else None
            cells.append("—" if rho is None else f"{rho:+.2f}")
            if rho is not None and o == MAIN_OUTCOME and rho < -0.3:
                flags.append((d, rho))
        L.append(f"| {d} | " + " | ".join(cells) + " |")
    L.append("")

    if flags:
        L += [f"## ⚠️ 方向可疑的维度（与主指标「{MAIN_OUTCOME}」负相关）", ""]
        for d, rho in flags:
            L.append(f"- **{d}**：与「{MAIN_OUTCOME}」秩相关 {rho:+.2f} —— "
                     f"这条判据打得越高，读者反而越不觉得值得存下来。"
                     f"去 `skills/eric-xhs-audit/SKILL.md` "
                     f"看维度「{d}」写了什么，对照实际稿子确认是不是判据本身错了。")
        L.append("")
    else:
        L += ["## 没有方向反了的维度", "",
              "所有维度与主指标的相关性都不为显著负，暂无需要推翻的判据（同卡样本范围内）。", ""]

    L += detail_table(paired)
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
