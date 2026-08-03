#!/usr/bin/env python3
"""评论区原话 → 案例库候选，让采集源源不断供给成稿。

为什么采集来的东西可以进案例库（2026-08-02 Eric 定）：素材是谁的不重要，能共鸣就行。
真红线只有两条 —— **不能编造**、**不能把别人的经历说成"我的"**。
所以案例库不再只收 Eric 自己的经历，用「来源」列区分，成稿时按来源决定人称：
  来源=自有 → 可用第一人称「我当时说」
  来源=采集 → 必须写成「有人在评论区说」「看到有人分享」

采集条目是半成品案例：有真实原话和处境，缺「结果」和「可迁移的那一句」，
标 状态=待确认 等人工补。即便一直不补，正文也能合法引用它的原话——
这正是当前扣分最狠的地方（「正文引语只有一条真人原话，其余全是脚本化改写」）。

用法：
  python3 harvest_cases.py             # 采集达标的新原话入库
  python3 harvest_cases.py --stats     # 只看评分分布，不写库
  python3 harvest_cases.py --min-score 4 --limit 10
"""
import argparse
import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CASES = SUCAI / "案例库.csv"
QUOTES = SUCAI / "评论区原话.csv"

BASE_FIELDS = ["案例ID", "场景", "对方原话", "我的原话", "结果", "可迁移的那一句", "已用于哪些笔记"]
NEW_FIELDS = ["来源", "来源链接", "状态"]
FIELDS = BASE_FIELDS + NEW_FIELDS

MIN_SCORE = 3
CONCRETE = re.compile(r"\d|[年月日天周]|[万千百]|工资|薪|领导|老板|HR|同事|经理|总监|面试官|部门")
DIALOGUE = re.compile(r"[""「」\"]|我说|他说|她说|领导说|老板说|HR说|回了一句|问我")
# 纯附和/感谢类评论：有具体名词也没有可引用的实质内容（「谢谢，明天要空降入职了」
# 「得了，19号就用」实测都被误判为 3 分）。附和词命中且全文短于 NOISE_LEN 即判噪音，
# 长的不算 —— 「说得真对，我就是担起了一个烂摊子…」以附和开头但后半段是真素材。
NOISE = re.compile(r"谢谢|感谢|学到了|说得对|说得真对|收藏了|马住|码住|打卡|加油|太有用|得了|好的$")
NOISE_LEN = 35
MIN_LEN = 20


def ensure_schema():
    """补齐新列。旧的手工条目一律标 来源=自有。

    幂等：已经有这三列就原样返回，可以反复跑。
    """
    rows = list(csv.DictReader(CASES.open(encoding="utf-8-sig")))
    if rows and all(c in rows[0] for c in NEW_FIELDS):
        return rows
    for r in rows:
        r["来源"] = r.get("来源") or "自有"
        r["来源链接"] = r.get("来源链接") or ""
        r["状态"] = r.get("状态") or "已确认"
    _write(rows)
    print(f"案例库补列 {NEW_FIELDS} —— 已有 {len(rows)} 行标为 来源=自有")
    return rows


def _write(rows):
    with CASES.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def score(q) -> tuple:
    """够不够格当案例素材。判据全部可机械核对，不做主观筛选。"""
    text = (q.get("用户原话") or "").strip()
    situation = (q.get("暴露的处境") or "").strip()
    if NOISE.search(text) and len(text) < NOISE_LEN:
        return 0, "纯附和无实质"
    s, why = 0, []
    if MIN_LEN <= len(text) <= 200:
        s += 1
        why.append("长度合适")
    if CONCRETE.search(text):
        s += 1
        why.append("有具体细节")
    if DIALOGUE.search(text):
        s += 1
        why.append("有对话感")
    if len(situation) >= 8:
        s += 1
        why.append("处境清晰")
    return s, "+".join(why)


def normalize(s: str) -> str:
    return re.sub(r"\s|[，。！？、,.!?~～]", "", s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=MIN_SCORE)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if not QUOTES.exists():
        print("评论区原话.csv 不存在")
        return 1

    rows = ensure_schema()
    seen = {normalize(r.get("我的原话", "")) for r in rows} | {normalize(r.get("对方原话", "")) for r in rows}
    harvested = [re.match(r"H(\d+)", r.get("案例ID", "")) for r in rows]
    next_id = max((int(m.group(1)) for m in harvested if m), default=0) + 1

    quotes = list(csv.DictReader(QUOTES.open(encoding="utf-8-sig")))
    scored = [(score(q), q) for q in quotes]

    if args.stats:
        dist = {}
        for (s, _), _q in scored:
            dist[s] = dist.get(s, 0) + 1
        print(f"评论区原话 {len(quotes)} 条，得分分布：")
        for s in sorted(dist, reverse=True):
            mark = "✅ 会入库" if s >= args.min_score else "  跳过"
            print(f"  {s} 分：{dist[s]:>3} 条  {mark}")
        return 0

    added = 0
    for (s, why), q in scored:
        if added >= args.limit:
            break
        text = (q.get("用户原话") or "").strip()
        if s < args.min_score or normalize(text) in seen:
            continue
        seen.add(normalize(text))
        rows.append({
            "案例ID": f"H{next_id:03d}",
            "场景": (q.get("暴露的处境") or "").strip(),
            "对方原话": "",
            "我的原话": text,
            "结果": "",
            "可迁移的那一句": "",
            "已用于哪些笔记": "",
            "来源": "采集",
            "来源链接": (q.get("来源链接") or "").strip(),
            "状态": "待确认",
        })
        next_id += 1
        added += 1

    if not added:
        print("没有新的达标原话（可能都已入库）")
        return 0
    _write(rows)
    print(f"✅ 入库 {added} 条采集案例（H{next_id-added:03d}–H{next_id-1:03d}），案例库共 {len(rows)} 行")
    print("   状态=待确认；「结果」「可迁移的那一句」留空，可在 case_entry.py 里补")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
