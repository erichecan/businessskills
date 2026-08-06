#!/usr/bin/env python3
"""标题体检 —— 拿 2026-08-04 那套实证规律去核对一条标题，并说清「为什么不合格」。

规则来源：skills/eric-xhs-title/SKILL.md「⭐ 最高优先级：Eric 账号实证规律」，
背后是 330 条搜索位笔记的实测（docs/20260804-标题真实规律-采集数据实证.md）。

为什么要单独做成模块：成稿预览页要逐条回答「这篇为什么没用新标题」。
把判定写在页面的 JS 里，就变成第二套规则，跟 skill 迟早对不上；
写在这里，将来 draft_check 想把它变成机械项也能直接调。

⚠️ 它判的是**形态**，不是好坏。「推翻预设」能不能被机械识别是有上限的——
标题里没有任何否定/反转词却确实推翻了某个预设，这里会误判。所以输出叫「疑似」，
最终以人和独立审核为准。宁可漏报也别让它有处置权（同 D7 的思路）。

用法：
  python3 title_check.py "汇报被领导打断，多半不是他没耐心"
  python3 title_check.py --all          # 体检全部成稿
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"

MAX_LEN = 20

# 推翻预设的显式标记。核心机制是「在一句话里否定读者脑子里已有的判断」，
# 而中文里干这件事几乎总会留下这些词之一。
OVERTURN = ["不是", "不再", "未必", "并非", "根本不", "其实", "原来", "反而", "别", "不要",
            "不止", "不在", "无关", "不管用", "没用", "多半不", "早就", "比你更", "先于你",
            "不看", "不考", "不问", "不用", "不必", "错了", "白", "反了",
            # 下面这批是被 SKILL「采用」表里的正例逼出来的：它们推翻预设但不带常见否定词。
            # 「领导不回消息的三种情况，追问方式完全不同」推翻的是「不回消息只有一种含义」，
            # 靠的是「不同」；「晋升答辩有可能不过，被刷的常是干最多的」推翻「干得多自然能过」，
            # 靠的是「常是」。少了它们，判定器会跟规则自己的样板打架。
            "不同", "有可能不", "常是", "才是", "改看", "真正"]

# 疑问词：−35 的最强负信号。搜索原句几乎都带它们，原样搬进标题即作废。
QUESTION = ["怎么办", "怎么接", "怎么说", "怎么回", "如何", "为什么", "该不该", "要不要",
            "会问什么", "问什么", "是不是", "有没有", "能不能", "吗", "呢", "?", "？"]

# 只复述读者已知的困境：−32。
KNOWN_PLIGHT = ["总被", "老被", "总是被", "又被", "怎么办", "好难", "好累", "崩溃"]

# 纯悬念（悬念党疲劳）：宣称"有这么一句"却不推翻任何认知。
SUSPENSE_ONLY = ["这句", "那句", "一句", "这招", "这个方法", "这几点", "这一点", "就行了"]

# 自夸式承诺。
BRAG = ["绝了", "绝杀", "封神", "神了", "yyds", "无敌", "必看", "速看", "血赚", "逆袭"]

# 写对方在想什么：+23，全表最强正信号。
OTHER_SIDE = ["面试官", "hr", "HR", "领导", "评委", "老板", "考官", "同事", "对方", "他"]


def check(title: str) -> dict:
    """返回 {verdict, score, hits, misses, why}。verdict ∈ 合格/疑似不合格/作废。"""
    t = (title or "").strip()
    hits, misses, fatal = [], [], []

    if not t:
        return {"verdict": "作废", "hits": [], "misses": ["标题为空"],
                "why": "没有解析到发布标题", "length": 0}

    n = len(t)
    if n > MAX_LEN:
        fatal.append(f"{n} 字超过 {MAX_LEN} 字上限")

    q = [w for w in QUESTION if w in t]
    if q:
        # 疑问句 −35 是全表最强负信号；挂在前半段同样作废（SKILL 五种直接作废第 1 条）
        fatal.append(f"含疑问词「{q[0]}」＝搜索原句形态（疑问句 −35）")

    plight = [w for w in KNOWN_PLIGHT if w in t]
    if plight and not any(w in t for w in OVERTURN):
        fatal.append(f"只复述读者已知的困境「{plight[0]}」（写困境 −32）")

    brag = [w for w in BRAG if w.lower() in t.lower()]
    if brag:
        fatal.append(f"自夸式承诺「{brag[0]}」")

    overturn = [w for w in OVERTURN if w in t]
    if overturn:
        hits.append(f"推翻预设：出现「{overturn[0]}」")
    else:
        susp = [w for w in SUSPENSE_ONLY if w in t]
        if susp:
            fatal.append(f"纯悬念不推翻认知「{susp[0]}」（悬念党疲劳 −10）")
        else:
            misses.append("看不出推翻了哪个预设——这是新规则的核心机制")

    other = [w for w in OTHER_SIDE if w in t]
    if other:
        hits.append(f"写对方那一侧：「{other[0]}」（+23，全表最强正信号）")
    else:
        misses.append("没写对方在想什么（面试官/领导/HR/评委）")

    if "，" in t or "、" in t:
        hits.append("前后两段结构（关键词 + 推翻）")

    if fatal:
        verdict = "作废"
        why = "；".join(fatal)
    elif misses and not overturn:
        verdict = "疑似不合格"
        why = "；".join(misses)
    else:
        verdict = "合格"
        why = "；".join(hits) or "无明显负信号"

    return {"verdict": verdict, "hits": hits, "misses": misses,
            "fatal": fatal, "why": why, "length": n}


# SKILL.md 里写死的样板：上面 5 条是 Eric 实盘筛选留下的「采用」，
# 下面 6 条是「⛔ 五种直接作废」的举例。判定器必须跟它们一致——
# 规则改了而这里没跟上，--selftest 会立刻炸出来，不会等到判错一批稿才发现。
GOLDEN_PASS = [
    "第三轮hr面试他不再看能力，改看这个",
    "汇报被领导打断，多半不是他没耐心",
    "领导不回消息的三种情况，追问方式完全不同",
    "晋升答辩被问倒，评委问的根本不是项目",
    "晋升答辩有可能不过，被刷的常是干最多的",
]
GOLDEN_FAIL = [
    "第三轮hr面试一般会问什么",              # 纯搜索原句复读
    "职场被孤立怎么办，动手的未必是同事",      # 疑问句挂前半段同样作废
    "汇报总被打断？",                        # 只复述已知困境
    "还有一句能救",                          # 纯悬念
    "把请您回复换成这句就行",                 # 纯悬念
    "这5招绝了",                             # 自夸式承诺
]


def selftest():
    bad = 0
    for t in GOLDEN_PASS:
        r = check(t)
        if r["verdict"] != "合格":
            print(f"⛔ 应判合格却判了「{r['verdict']}」：{t}\n     {r['why']}")
            bad += 1
    for t in GOLDEN_FAIL:
        r = check(t)
        if r["verdict"] == "合格":
            print(f"⛔ 应判不合格却判了合格：{t}")
            bad += 1
    print(f"{'⛔ 自检失败 ' + str(bad) + ' 条' if bad else '✅ 自检通过'}"
          f"（{len(GOLDEN_PASS)} 正例 + {len(GOLDEN_FAIL)} 反例，样板取自 SKILL.md）")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?")
    ap.add_argument("--all", action="store_true", help="体检全部成稿的发布标题")
    ap.add_argument("--selftest", action="store_true", help="拿 SKILL.md 的正反例校验判定器")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.all:
        sys.path.insert(0, str(REPO / "scripts" / "case-entry"))
        from case_entry import parse_draft
        files = sorted(SUCAI.glob("成稿_*.md")) + sorted((SUCAI / "归档稿").glob("成稿_*.md"))
        icon = {"合格": "✅", "疑似不合格": "⚠️", "作废": "⛔"}
        for f in files:
            title = parse_draft(f.read_text(encoding="utf-8")).get("title", "")
            r = check(title)
            print(f"{icon[r['verdict']]} {r['length']:>2}字 {title[:26]:<28}{r['why'][:52]}")
            print(f"      └ {f.name}")
        return 0

    if not args.title:
        ap.error("给一条标题，或用 --all")
    r = check(args.title)
    print(f"{r['verdict']}（{r['length']} 字）")
    for h in r["hits"]:
        print(f"  ✅ {h}")
    for m in r.get("fatal", []):
        print(f"  ⛔ {m}")
    for m in r["misses"]:
        print(f"  ⚠️ {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
