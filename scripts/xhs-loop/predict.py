#!/usr/bin/env python3
"""成稿数据预测 —— 每篇稿发布前先押个数，7 天后拿真实数据对账。

为什么要押数：不押数就没法复盘。「这篇效果不好」是感觉，「预测观看 400 实际 120，
低密度词的搜索盘子被高估了 3 倍」才是能改进的结论。

⚠️ v1 是**先验模型不是拟合模型**。本项目只有 2 篇已发布笔记且都是发布当天数据，
样本量不足以拟合任何东西。所有系数标了来源，见 docs/20260803-小红书数据预测调研.md，
等 review_prediction.py 攒够 5 篇 7 天数据再改成拟合值。

输出区间不输出点估计：样本不足时给点估计是假精确。复盘只判「实际是否落在区间内」。

用法：
  python3 predict.py 成稿_2026-08-03_xxx.md            # 预测并写入 预测记录.csv
  python3 predict.py 成稿_xxx.md --lane 推荐流
  python3 predict.py 成稿_xxx.md --dry-run             # 只打印不落盘
"""
import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
PROBE_DIR = SUCAI / "探测原始"
PRED = SUCAI / "预测记录.csv"

PRED_COLS = ["预测日", "成稿文件", "关键词", "口径", "竞争密度", "意图强度", "审核分",
             "观看_低", "观看_高", "点赞_低", "点赞_高", "收藏_低", "收藏_高",
             "评论_低", "评论_高", "转发_低", "转发_高", "CES_低", "CES_高", "依据"]

# ---- 先验系数（每一条都可被 7 天数据证伪，见调研文档第四节）----

# 7 天观看基线。低密度词竞争小排得上但盘子小；高密度词盘子大但前排被高赞占满，方差最大。
VIEWS_BASE = {"低": (250, 600), "中": (350, 900), "高": (200, 700)}

# 内容质量在搜索排名里占 40%，是单项权重最高的
def quality_factor(score):
    if score is None:
        return 0.85          # 没审核分时取偏保守值，不假装知道
    if score >= 90:
        return 1.3
    if score >= 85:
        return 1.0
    return 0.7

# 互动率。全域平均 3%，第二阶流量池门槛 5%。意图强度高 = 来的人带着问题，更愿意互动。
RATE_BASE = {"高": (0.045, 0.075), "中": (0.030, 0.055), "低": (0.020, 0.040)}

# 相对点赞的比例。搜索流收藏意愿显著高于推荐流（收藏=我要用，不是我喜欢）。
RATIOS = {
    "搜索流": {"收藏": (0.6, 1.0), "评论": (0.10, 0.20), "转发": (0.08, 0.15)},
    "推荐流": {"收藏": (0.3, 0.5), "评论": (0.10, 0.15), "转发": (0.05, 0.10)},
}


def read_rows(path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig"))) if path.exists() else []


def keyword_of(draft: Path) -> str:
    """成稿头部会写「关键词来源：词库.csv「XXX」」，从那里取。"""
    text = draft.read_text(encoding="utf-8")
    m = re.search(r"词库\.csv[『「\"']([^』」\"']+)[』」\"']", text)
    if m:
        return m.group(1).strip()
    for r in read_rows(CIKU):                    # 兜底：全文匹配词库里的词
        kw = (r.get("关键词") or "").strip()
        if kw and kw in text:
            return kw
    return ""


def audit_score_of(fname: str):
    scores = [r for r in read_rows(AUDIT_LOG)
              if (r.get("成稿文件") or "").strip() == fname
              and (r.get("审核方") or "").strip() == "独立审核"]
    if not scores:
        return None
    try:
        return int((scores[-1].get("总分") or "").strip())
    except ValueError:
        return None


CALIB = SUCAI / "预测校准.csv"
CALIB_MIN_SAMPLE = 5      # 与 预测复盘.md 头部那句「累计 ≥5 篇才允许改系数」同一口径


def calibration_hint(density: str, med) -> str:
    """把历史实测偏差摆出来。**只报数字，不自动改系数。**

    ⛔ 2026-08-13 加，这是「复盘结论 → 预测模型」这条通道的第一段。
    此前复盘写出的结论（例如「VIEWS_BASE['低'] 应按 median_likes 拆两档」）
    只进 预测复盘.md，而全仓库没有第二个脚本读那个 md —— 数据跑到复盘就断了。

    为什么只提示不自动改：复盘自己立的规矩是「累计 ≥5 篇有 7 天数据才允许改系数」，
    而现在只有 1 篇。自动拟合会拿单篇噪声当规律，比不校准更糟。
    样本够了以后要不要改、改成多少，仍然是人拍板 —— 这里只保证他手上有数。
    """
    rows = read_rows(CALIB)
    if not rows:
        return ""
    same = [r for r in rows if r.get("指标") == "观看" and (r.get("密度") or "") == density]
    if not same:
        return ""
    # ⛔ 按**关键词**去重，不是按行数。同一个关键词写过好几篇稿，只有一篇真发布，
    # 那几篇会对账到同一份实际数据、给出同一个倍数 —— 按行数算等于把 1 个样本
    # 当成 6 个，「≥5 篇才改系数」这条闸门就形同虚设（实测 9 行只有 2 个独立值）。
    by_kw = {}
    for r in same:
        try:
            m = float(r.get("倍数") or 0)
        except ValueError:
            continue
        if m > 0:
            by_kw[(r.get("关键词") or r.get("成稿文件") or "")] = m
    mults = sorted(by_kw.values())
    if not mults:
        return ""
    mults.sort()
    mid = mults[len(mults) // 2]
    tail = (f"（样本 {len(mults)} 篇 < {CALIB_MIN_SAMPLE}，仅供参考，不足以改系数）"
            if len(mults) < CALIB_MIN_SAMPLE
            else f"（样本已达 {len(mults)} 篇，**可以考虑校准 VIEWS_BASE 了**）")
    lows = [r.get("前排中位赞") for r in same if r.get("前排中位赞")]
    extra = f"，这些样本的前排中位赞：{'/'.join(lows[:5])}" if lows else ""
    return (f"📉 历史实测：密度「{density}」的观看预测，实际/预测中位 = {mid:.2f}×{extra}\n"
            f"   {tail}")


def median_likes_of(keyword: str):
    for f in sorted(PROBE_DIR.glob("probe_*.json")) if PROBE_DIR.exists() else []:
        if f.name.endswith(".result.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("keyword") == keyword:
            return (d.get("density") or {}).get("median_likes")
    return None


def predict(density, intent, score, lane):
    vlo, vhi = VIEWS_BASE.get(density, VIEWS_BASE["中"])
    q = quality_factor(score)
    views = (round(vlo * q), round(vhi * q))

    rlo, rhi = RATE_BASE.get(intent, RATE_BASE["中"])
    # 互动总量 = 观看 × 互动率，其中点赞是主体，其余按比例挂靠
    inter = (views[0] * rlo, views[1] * rhi)
    ratios = RATIOS.get(lane, RATIOS["搜索流"])
    # 总互动 = 赞 × (1 + 藏比 + 评比 + 转比)，反解出赞
    div_lo = 1 + ratios["收藏"][0] + ratios["评论"][0] + ratios["转发"][0]
    div_hi = 1 + ratios["收藏"][1] + ratios["评论"][1] + ratios["转发"][1]
    likes = (round(inter[0] / div_hi), round(inter[1] / div_lo))

    out = {"观看": views, "点赞": likes}
    for k, (a, b) in ratios.items():
        out[k] = (round(likes[0] * a), round(likes[1] * b))
    out["CES"] = (round(likes[0] + out["收藏"][0] + out["评论"][0] * 4 + out["转发"][0] * 4),
                  round(likes[1] + out["收藏"][1] + out["评论"][1] * 4 + out["转发"][1] * 4))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", help="成稿文件名")
    ap.add_argument("--lane", default="搜索流", choices=["搜索流", "推荐流"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    draft = SUCAI / args.draft
    if not draft.exists():
        draft = SUCAI / "归档稿" / args.draft
    if not draft.exists():
        print(f"找不到 {args.draft}", file=sys.stderr)
        return 2

    kw = keyword_of(draft)
    row = next((r for r in read_rows(CIKU) if (r.get("关键词") or "").strip() == kw), {})
    density = (row.get("竞争密度") or "中").strip() or "中"
    intent = (row.get("意图强度") or "中").strip() or "中"
    score = audit_score_of(draft.name)
    med = median_likes_of(kw)

    p = predict(density, intent, score, args.lane)
    # fallback 要写进依据里。复盘时「这条是按中档估的」和「这条真的是中密度」
    # 是两种完全不同的偏差原因，混在一起就找不出模型错在哪。
    fb = []
    if density not in VIEWS_BASE:
        fb.append(f"密度「{density}」未探测→按中档估")
    if intent not in RATE_BASE:
        fb.append(f"意图「{intent}」未知→按中档估")
    if score is None:
        fb.append("无审核分→质量系数取保守值 0.85")
    basis = (f"密度{density}(前排中位{med if med is not None else '?'}赞)·意图{intent}·"
             f"审核{score if score is not None else '未审'}·质量系数{quality_factor(score)}"
             + ("｜" + "；".join(fb) if fb else ""))

    print(f"【{draft.name}】{args.lane} · 关键词「{kw}」")
    print(f"依据：{basis}")
    print(f"{'':6}{'低':>8}{'高':>8}")
    for k in ["观看", "点赞", "收藏", "评论", "转发", "CES"]:
        print(f"{k:6}{p[k][0]:>8}{p[k][1]:>8}")
    if p["评论"][1] < 1:
        print("⚠️ 评论预测不足 1 条 —— CES 里评论权重 ×4，这是最该争的项")
    print(calibration_hint(density, med))

    if args.dry_run:
        return 0
    new = not PRED.exists()
    with PRED.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(PRED_COLS)
        w.writerow([date.today().isoformat(), draft.name, kw, args.lane, density, intent,
                    score if score is not None else "",
                    *[x for k in ["观看", "点赞", "收藏", "评论", "转发", "CES"] for x in p[k]],
                    basis])
    print(f"\n已写入 预测记录.csv（7 天后由 review_prediction.py 对账）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
