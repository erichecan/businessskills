#!/usr/bin/env python3
"""词库去重 —— 合并同一概念的措辞变体，别让它们各自占选题/探测名额。

## 起因（2026-09-12）

查"为什么攒了 8k 素材却写不出稿"时发现：176 个「已验证」词里有一批像
「HR问'你的缺点'该怎么回答(一篇讲清楚)」「HR问"你的缺点"该怎么回答（一篇讲清楚）文字版」
这样只是标点/措辞不同的变体，本质是同一个问题。这些变体：
  · 探测阶段各自占用一个探测名额（20词/天的硬限流下，等于白烧配额）
  · pick_topic 挑中其中一个后，写出来的标题很容易撞上另一变体已经写过的稿子，
    被 draft_check.title_similarity()（≥30% 判重复）拦下重写，3 轮返工用光就归档

去重判据复用 draft_check.title_similarity()（同一套字符 Jaccard + difflib 算法，
理由见该文件注释）。阈值单独标定，不跟标题共用 0.30：
  · 抽样已知变体（"HR问你的缺点"5 个变体）两两相似度 0.92～1.00
  · 随机 40 个不相关词两两比对，最高只到 0.63（"约面试前需要问清楚什么" vs
    "面试反问要问什么"，勉强算相关但不是同一问题）
  · 但 0.80～0.84 这一段人工抽查发现假阳性：「面试答不上来会被录取吗」vs
    「面试答不上来会被嘲讽吗」相似度 82%——只换了一个等长词，字符类算法分不出这是
    两个完全不同的问题。收紧到 0.85 才把这类误伤过滤掉，同时仍稳稳盖住已知的
    真变体（下限 0.92，留了 7 个点余量）。

## 用法

  python3 dedupe_keywords.py --dry-run     # 只看会合并哪些组，不落盘
  python3 dedupe_keywords.py               # 落盘：多余变体状态改「放弃」
  python3 dedupe_keywords.py --threshold 0.85
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill  # noqa: E402  — 复用 CIKU 路径 / read_csv / backup（同一份词库.csv）

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xhs-health"))
from draft_check import title_similarity  # noqa: E402

CIKU = backfill.CIKU
THRESHOLD = 0.85

# 只在「还没定型」的词里去重：已验证/候选是活跃池，会被 pick_topic / probe 选中；
# 排队·已出稿·待发布·已发布是已经绑定到具体稿子的词，动它们等于篡改发布记录。
ACTIVE = lambda s: s == "已验证" or s.startswith("候选")  # noqa: E731


def _keeper_rank(row):
    """代表词优先级：已验证 > 候选；已判定竞争密度的 > 待探测；词短的更精炼。

    数值越小越优先——sort 直接用。
    """
    verified = 0 if row.get("状态", "").strip() == "已验证" else 1
    probed = 0 if row.get("竞争密度", "").strip() not in ("", "待探测") else 1
    return (verified, probed, len(row["关键词"].strip()))


def find_clusters(rows, threshold):
    """并查集：相似度 ≥ threshold 的两个词合并到同一簇。"""
    idx = list(range(len(rows)))
    parent = list(idx)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    pairs = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            s = title_similarity(rows[i]["关键词"], rows[j]["关键词"])
            if s >= threshold:
                pairs.append((s, i, j))
                union(i, j)

    groups = {}
    for i in idx:
        groups.setdefault(find(i), []).append(i)
    best_score = {}
    for s, i, j in pairs:
        r = find(i)
        best_score[r] = max(best_score.get(r, 0.0), s)
    return [(members, best_score[root]) for root, members in groups.items() if len(members) > 1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只打印会合并哪些组，不落盘")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    args = ap.parse_args()

    rows = backfill.read_csv(CIKU)
    active_idx = [i for i, r in enumerate(rows) if r.get("关键词", "").strip()
                  and ACTIVE(r.get("状态", "").strip())]
    active_rows = [rows[i] for i in active_idx]
    print(f"参与去重 {len(active_rows)} 个词（已验证/候选，排队及以后的不动）")

    clusters = find_clusters(active_rows, args.threshold)
    if not clusters:
        print("没有发现相似度达标的变体，不用合并")
        return 0

    today = date.today().isoformat()
    merged = 0
    for members, score in sorted(clusters, key=lambda c: -c[1]):
        group_rows = [active_rows[m] for m in members]
        group_rows.sort(key=_keeper_rank)
        keeper, dups = group_rows[0], group_rows[1:]
        print(f"\n[相似度 {score*100:.0f}%] 保留「{keeper['关键词']}」"
              f"（{keeper.get('状态')}/{keeper.get('竞争密度') or '待探测'}），合并掉：")
        for d in dups:
            print(f"   ⛔ 「{d['关键词']}」（{d.get('状态')}）")
            if not args.dry_run:
                old_note = (d.get("备注") or "").strip()
                note = f"与「{keeper['关键词']}」相似度{score*100:.0f}%，判重复变体，自动去重合并（{today}）"
                d["备注"] = f"{old_note}；{note}" if old_note else note
                d["状态"] = "放弃"
            merged += 1

    print(f"\n{'（--dry-run，未落盘）' if args.dry_run else ''}"
          f"共 {len(clusters)} 组变体，{merged} 个词被判重复")
    if not args.dry_run:
        backfill.backup(CIKU)
        cols = list(rows[0].keys()) if rows else backfill.CIKU_COLS
        backfill.write_csv(CIKU, rows, cols)
        print(f"✅ 已写回 {CIKU.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
