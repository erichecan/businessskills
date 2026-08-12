#!/usr/bin/env python3
"""互动比例分析 · 从采集数据重算「评论/赞」与「藏/赞」。

存在的理由（2026-08-12）：
  知识框架 §十六「评论率专项」用的口径是 **评论/观看**（自家 6/1992 = 0.30%），
  据此判定"评论少 = CTA 设计不行"，并推出「CTA 必须回一个字母」这条硬拦截。
  但采集不到别人的观看数，能采到的只有赞/藏/评 —— 可比的口径是 **评论/赞**。
  这个脚本就是把可比口径算出来，用他人真实数据给自家指标一个分母。

用法：
  python3 scripts/xhs-health/engage_ratio.py                # 打印报告
  python3 scripts/xhs-health/engage_ratio.py --write        # 同时落盘 md
  python3 scripts/xhs-health/engage_ratio.py --min-samples 30

⚠️ 样本量不够时脚本会明确拒绝给结论 —— 5 条样本算出来的中位数不是证据。
"""
import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROBE_DIR = REPO / "xhs" / "素材库" / "探测原始"
OUT_MD = REPO / "xhs" / "素材库" / "互动比例基线.md"

# 低于这个样本数不出结论。30 是「能看出分布形状」的下限，不是统计学魔法数字。
#
# ⚠️ 阈值卡的是**可比组**的条数，不是总条数。2026-08-12 第一轮 23 条数据的发现：
#   ≥1万赞组 评论/赞中位 0.76%，而 <100 赞组是 7.14% —— 差一个数量级。
#   自家账号是几十个赞的量级，拿大号的 0.76% 去推「30 赞该有几条评论」会把期望值
#   压低约 10 倍，正好得出「0 条评论很正常」这个让人放心的结论。**方向刚好是错的。**
#   所以可比的是 <1000 赞组，采集也要往低赞笔记补，不能只堆高赞样本。
DEFAULT_MIN_SAMPLES = 30
COMPARABLE_MAX_LIKES = 1000  # 自家账号所在的量级，比例只从这个组里取

# 赞量级分层：比例可能随量级变化（大号的评论率未必适用于小号）
TIERS = [
    ("<100", 0, 100),
    ("100-1k", 100, 1000),
    ("1k-1万", 1000, 10000),
    ("≥1万", 10000, 10**9),
]


def parse_num(raw):
    """'1361' → 1361；'3.5万' → 35000；'赞'/None/'' → None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"^([\d.]+)\s*k$", s, re.I)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return None


def load_samples():
    """扫全部 probe JSON，抽出有赞有评的互动样本。按 note_id 去重（同一笔记会被多个词采到）。

    ⚠️ 「0 评论」与「没采到」必须分开，否则最关键的判据会被系统性低估：
    小红书评论数为 0 时按钮显示的是「评论」二字而不是数字，parse_num 返回 None。
    若把它当采集失败丢掉，**恰恰是 0 评论的笔记被剔出样本**，
    「低赞笔记有多少篇 0 评论」就永远算不出真值。
    判据：`engage_bar_raw` 非空 = 互动栏确实渲染了 → 解析不出数字就是 0；
          `engage_bar_raw` 为空 = 整条没采到 → 才算丢弃，并计数报出来。
    """
    by_note = {}
    files = 0
    dropped = 0
    for p in sorted(PROBE_DIR.glob("probe_*.json")):
        if p.name.endswith(".result.json"):
            continue
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 跳过 {p.name}：{e}", file=sys.stderr)
            continue
        files += 1
        for s in data.get("engage_samples") or []:
            likes = parse_num(s.get("like_raw")) or s.get("likes_from_card")
            comments = parse_num(s.get("comment_raw"))
            collects = parse_num(s.get("collect_raw"))
            # 计数为 0 时小红书显示的是「评论」「收藏」这类中文标签而不是数字，
            # parse_num 返回 None。互动栏确实渲染了就按 0 算，别当采集失败丢掉。
            bar_rendered = bool((s.get("engage_bar_raw") or "").strip())
            if bar_rendered:
                if comments is None:
                    comments = 0
                if collects is None:
                    collects = 0
            if not likes or comments is None:
                dropped += 1
                continue
            nid = s.get("note_id") or f"{p.name}:{s.get('rank')}"
            row = {
                "note_id": nid,
                "keyword": data.get("keyword"),
                "probed_at": data.get("probed_at"),
                "title": s.get("title"),
                "likes": likes,
                "collects": collects,
                "comments": comments,
                "body_len": s.get("body_len"),
                "c_over_l": comments / likes * 100,
                "s_over_l": collects / likes * 100 if collects else None,
            }
            # 同一笔记多次采到时保留赞数更高的那次（更晚的观测）
            if nid not in by_note or likes > by_note[nid]["likes"]:
                by_note[nid] = row
    return list(by_note.values()), files, dropped


def pct(vals, q):
    if not vals:
        return None
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    i = (len(vals) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (i - lo)


def describe(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "min": min(vals),
        "p25": pct(vals, 0.25),
        "median": statistics.median(vals),
        "p75": pct(vals, 0.75),
        "max": max(vals),
    }


def fmt(d, unit="%"):
    if not d:
        return "—"
    return (f"n={d['n']} · 中位 {d['median']:.2f}{unit} · "
            f"四分位 {d['p25']:.2f}–{d['p75']:.2f}{unit} · "
            f"极值 {d['min']:.2f}–{d['max']:.2f}{unit}")


def build_report(rows, files, dropped, min_samples):
    lines = []
    A = lines.append
    comparable = [r for r in rows if r["likes"] < COMPARABLE_MAX_LIKES]
    enough = len(comparable) >= min_samples

    A(f"# 互动比例基线（评论/赞 · 藏/赞）")
    A("")
    A(f"> 生成：{date.today().isoformat()} · 由 `scripts/xhs-health/engage_ratio.py` 重跑覆盖，不要手改。")
    A(f"> 数据源：`xhs/素材库/探测原始/` 共 {files} 个 probe 文件，去重后 **{len(rows)} 条**他人笔记，"
      f"其中 <{COMPARABLE_MAX_LIKES} 赞的**可比样本 {len(comparable)} 条**。")
    A(f"> 证据等级：**C（他人采集）**。口径为 评论/赞，因为采不到他人观看数。")
    if dropped:
        A(f"> ⚠️ 另有 **{dropped} 条**互动栏整个没采到（`engage_bar_raw` 为空）被排除。"
          f"若这个数持续变大，是选择器失效的信号，去查 probe.py 的 engage 段。")
    A("")

    if not enough:
        A(f"## ⛔ 可比样本不足，本次不出结论")
        A("")
        A(f"可比样本（<{COMPARABLE_MAX_LIKES} 赞）{len(comparable)} 条 < 阈值 {min_samples} 条。继续采集：")
        A("")
        A("```bash")
        A("python3 scripts/xhs-probe/probe.py --from-cikuku --limit 5   # 每次约 10 分钟")
        A("```")
        A("")
        A("⚠️ 卡的是**可比样本**条数而非总条数：比例随赞量级差一个数量级（见分层表），")
        A("拿大号的比例推小号会把期望值压低约 10 倍。总样本堆再多也代替不了低赞样本。")
        A("")
        A("下面的数字**仅供观察**，不得作为改规则的依据。")
        A("")

    A("## 总体")
    A("")
    A(f"- 评论/赞：{fmt(describe(rows, 'c_over_l'))}")
    A(f"- 藏/赞：{fmt(describe(rows, 's_over_l'))}")
    A("")

    A("## 按赞量级分层")
    A("")
    A("| 量级 | 笔记数 | 评论/赞 中位 | 评论/赞 四分位 | 藏/赞 中位 |")
    A("|---|---:|---:|---|---:|")
    for name, lo, hi in TIERS:
        sub = [r for r in rows if lo <= r["likes"] < hi]
        if not sub:
            A(f"| {name} | 0 | — | — | — |")
            continue
        c = describe(sub, "c_over_l")
        s = describe(sub, "s_over_l")
        s_med = f"{s['median']:.2f}%" if s else "—"
        A(f"| {name} | {len(sub)} | {c['median']:.2f}% | "
          f"{c['p25']:.2f}–{c['p75']:.2f}% | {s_med} |")
    A("")

    A("## 对自家账号的含义")
    A("")
    med = describe(comparable, "c_over_l")
    med_all = describe(rows, "c_over_l")
    if med:
        all_note = f"（总体中位 {med_all['median']:.2f}%）" if med_all else ""
        A(f"⚠️ 下表只用**可比组**（<{COMPARABLE_MAX_LIKES} 赞，n={med['n']}）的中位 "
          f"**{med['median']:.2f}%**，不用总体中位{all_note} "
          f"—— 比例随量级变化，混算会得出偏低的期望值。")
        A("")
        A("| 自家笔记的赞数 | 评论数的期望值 |")
        A("|---:|---:|")
        for likes in (10, 30, 50, 100, 500):
            A(f"| {likes} | {likes * med['median'] / 100:.1f} |")
        A("")
        exp30 = 30 * med["median"] / 100
        A(f"→ 30 个赞对应约 **{exp30:.1f} 条**评论。判定「0 条评论是否异常」看这个数：")
        A(f"  期望值 < 1 → 0 条在统计上正常，不构成「CTA 设计失败」的证据；")
        A(f"  期望值 ≥ 2 → 0 条确实偏低，CTA 的怀疑才立得住。")
        A("")
        A(f"⛔ **但比例这个口径在低赞段本身是偏高的**，别直接用上表下结论。")
        A(f"评论数是整数且非零下限为 1：一篇 17 赞的笔记只要有 1 条评论，比例就是 5.9%，")
        A(f"而这 1 条评论可能只是作者自己或一个熟人。赞越少，这个离散化偏差把比例抬得越高。")
        A(f"**更硬的判据是下面这条 —— 直接数有多少低赞笔记根本没有评论。**")
    A("")

    A("## 低赞笔记里，多少篇是 0 评论？（不受离散化偏差影响的判据）")
    A("")
    if comparable:
        zero = [r for r in comparable if r["comments"] == 0]
        A(f"可比组 {len(comparable)} 篇中，**{len(zero)} 篇评论数为 0**"
          f"（{len(zero) / len(comparable) * 100:.0f}%）。")
        A("")
        A(f"- 若这个占比高（多数低赞笔记都是 0 评论）→ 自家 0 评论属常态，**CTA 不是根因**；")
        A(f"- 若这个占比低（低赞笔记普遍也有 1-2 条）→ 自家 0 评论确实反常，CTA 的怀疑成立。")
        A("")
        A(f"⚠️ 观测限制：probe 采的低赞笔记来自**搜索结果前排**，本身已有曝光；")
        A(f"自家笔记未必在同一分布里。这条判据能证伪「0 评论 = CTA 失败」，")
        A(f"但反过来不足以证实它。")
    else:
        A("可比组为空，无法计算。")
    A("")

    A("## 全部样本")
    A("")
    A("| 赞 | 藏 | 评 | 评/赞 | 藏/赞 | 正文字数 | 标题 |")
    A("|---:|---:|---:|---:|---:|---:|---|")
    for r in sorted(rows, key=lambda x: -x["likes"]):
        collects = r["collects"] if r["collects"] is not None else "—"
        s_ratio = f"{r['s_over_l']:.0f}%" if r["s_over_l"] is not None else "—"
        body = r["body_len"] if r["body_len"] is not None else "—"
        title = (r["title"] or "").replace("|", "｜")[:30]
        A(f"| {r['likes']} | {collects} | {r['comments']} | "
          f"{r['c_over_l']:.2f}% | {s_ratio} | {body} | {title} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="落盘到 xhs/素材库/互动比例基线.md")
    ap.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    args = ap.parse_args()

    rows, files, dropped = load_samples()
    report = build_report(rows, files, dropped, args.min_samples)
    print(report)
    if args.write:
        OUT_MD.write_text(report + "\n")
        print(f"\n→ 已写入 {OUT_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
