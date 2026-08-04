#!/usr/bin/env python3
"""关键词池 → 词库：把采集攒下的候选词导进探测队列。

2026-08-03 查出来的断点：关键词池有 673 个候选词，词库只有 186 行，两者**只重叠 5 个**。
也就是说采集每天挖出的词，668 个卡在关键词池里从没进过探测队列——
「候选积压 666」不是探测太慢，是管道根本没接上。probe.py 只从词库取词，
关键词池里的词再多也不会被探测，自然也永远变不成成稿。

筛选是机械的（判据全部可核对，不经模型）：
  硬门槛：长度 ≥6 · 属于职场表达赛道 · 不是打卡/课程/资料类
  排序：问句优先，其次热度、长度
关键词池里混着「100天口才训练打卡」「麦肯锡100个关键词法」这类词，
全量导入等于把噪音塞满探测队列，每个词探测要 45-90 秒，浪费不起。

⚠️ 问句是排序信号不是硬门槛。初版拿它当门槛，一下挡掉 343 个词，其中
「面试成功的征兆」「面试暗示你已经通过了」都是有真实搜索量的陈述式长尾词，
放宽后通过数从 139 → 564。

用法：
  python3 import_pool.py --stats            # 只看筛选漏斗，不写库
  python3 import_pool.py --dry-run          # 同上（看将导入哪些）
  python3 import_pool.py --limit 150        # 导入前 150 个（问句优先）
"""
import argparse
import csv
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
POOL = SUCAI / "关键词池.csv"
CIKU = SUCAI / "词库.csv"

MIN_LEN = 6
# 问句是**加分项不是必要条件**。初版把它当硬门槛，挡掉了 343 个词，其中
# 「面试成功的征兆」「面试暗示你已经通过了」都是有真实搜索量的陈述式长尾词。
# 改成排序信号：问句优先探，陈述式排后面，但都进队列。
QUESTION = re.compile(r"怎么|如何|要不要|能不能|为什么|什么|吗|该不该|还是|多少|哪|会不会|需不需要|算不算")
DOMAIN = re.compile(r"面试|应聘|谈薪|薪资|涨薪|加薪|工资|调薪|汇报|答辩|述职|绩效|领导|老板|上司|同事|职场|"
                    r"晋升|试用期|离职|裁员|入职|简历|HR|hr|offer|跳槽|空降|团队|下属|年终奖|背调|"
                    r"反问|终面|群面|复试|转正|调岗|请假|加班|竞业|实习|自我介绍|话术|结构化|口才|"
                    r"沟通|表达|谈判|职业规划|工作经历|优缺点|松弛感|上班|同级|甩锅|背锅")
# 这些是内容形式/资料类词，不是搜索问句，探了也没用
EXCLUDE = re.compile(r"打卡|训练营|课程|模板下载|资料|书单|电子书|直播回放|课件|training")

CIKU_COLS = ["关键词", "场景域", "场景类型", "意图强度", "竞争密度", "人工初判",
             "关联案例ID", "状态", "发布日", "笔记链接", "搜索来源占比"]


def read(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def judge(kw: str):
    if len(kw) < MIN_LEN:
        return False, "太短（宽泛词，搜索位早被占满）"
    if EXCLUDE.search(kw):
        return False, "资料/课程类，不是搜索问句"
    if not DOMAIN.search(kw):
        return False, "不在职场表达赛道"
    return True, "通过"


def heat(row):
    try:
        return float((row.get("平均热度") or "0").strip() or 0)
    except ValueError:
        return 0.0


def rank(row):
    """探测队列的优先级：问句 > 陈述式，同级按热度、按长度（长句更可能是空缺位）。

    热度列在候选行里普遍为空，所以它只能当次级信号——排序主要靠是不是问句。
    """
    kw = (row.get("关键词") or "").strip()
    return (0 if QUESTION.search(kw) else 1, -heat(row), -len(kw))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    pool = [r for r in read(POOL) if (r.get("类型") or "").strip() == "候选"]
    ciku = read(CIKU)
    have = {(r.get("关键词") or "").strip() for r in ciku}

    passed, rejected = [], {}
    for r in pool:
        kw = (r.get("关键词") or "").strip()
        if not kw or kw in have:
            continue
        ok, why = judge(kw)
        if ok:
            passed.append(r)
        else:
            rejected.setdefault(why, []).append(kw)

    passed.sort(key=rank)

    if args.stats or args.dry_run:
        print(f"关键词池候选 {len(pool)} 个 · 已在词库 {len(pool) - len(passed) - sum(len(v) for v in rejected.values())} 个")
        print(f"筛选通过 {len(passed)} 个：")
        for why, kws in sorted(rejected.items(), key=lambda x: -len(x[1])):
            print(f"  ✗ {why}：{len(kws)} 个    例：{'、'.join(kws[:3])}")
        print(f"\n本次将导入前 {min(args.limit, len(passed))} 个（问句优先）：")
        for r in passed[:args.limit][:15]:
            print(f"  {'问句' if QUESTION.search(r['关键词']) else '陈述'}  {r['关键词']}")
        if len(passed) > 15:
            print(f"  …… 还有 {min(args.limit, len(passed)) - 15} 个")
        if args.dry_run or args.stats:
            return 0

    todo = passed[:args.limit]
    if not todo:
        print("没有可导入的词（都已在词库或未通过筛选）")
        return 0
    for r in todo:
        row = {c: "" for c in CIKU_COLS}
        row.update({"关键词": r["关键词"].strip(), "场景域": "待归类", "场景类型": "待归类",
                    "意图强度": "待探测", "竞争密度": "待探测", "状态": "候选"})
        ciku.append(row)
    with CIKU.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CIKU_COLS)
        w.writeheader()
        for r in ciku:
            w.writerow({c: r.get(c, "") for c in CIKU_COLS})
    print(f"✅ 导入 {len(todo)} 个词到词库（状态=候选 密度=待探测），词库共 {len(ciku)} 行")
    print("   下一步：probe.py 会自动从待探测队列取词，探完 auto_analyze.py → backfill.py 定级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
