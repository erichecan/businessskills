#!/usr/bin/env python3
"""文风机械指标测量 — L1 的量尺，把「正文网感/可信度差异/开头钩子」从主观分挪进代码。

只测量，不判定。阈值由 measure 的实际分布标定后，再写进 draft_check.py。

用法：
  python3 style_metrics.py                 # 测全部成稿
  python3 style_metrics.py --file 成稿_x.md  # 测单篇
  python3 style_metrics.py --show 排比      # 打印某指标的命中实例，供人工核验
"""
import argparse
import re
import statistics
import sys
from pathlib import Path

SUCAI = Path(__file__).resolve().parents[2] / "xhs" / "素材库"

# 书面语连接词：AI 味最稳定的信号，人写口语稿几乎不用
FORMAL_CONJ = ["因此", "然而", "从而", "进而", "并非", "而是", "换言之", "由此可见",
               "与此同时", "综上", "反之", "亦即", "故而", "继而", "此外", "更进一步"]
COLLOQUIAL = ["其实", "说白了", "真的", "就这么", "这不就", "嘿嘿", "咱", "别急",
              "你看", "对吧", "哦", "啊", "呢", "吧", "嘛", "哈", "太", "特别"]
ROLE_WORDS = ["HR", "hr", "面试官", "评委", "总监", "经理", "老板", "领导", "同事",
              "下属", "候选人", "主管", "leader", "Leader", "组长", "总经理",
              # 2026-08-02 补：结构化面试稿通篇用「考官」，原表漏收导致密度被低估
              "考官", "主考官", "应聘者", "求职者", "用人部门", "HRBP"]
NUM_RE = re.compile(r"\d+|[一二三四五六七八九十百千万]{1,4}(?=[个次天周月年分秒岁人条句轮元万块])")
TIME_RE = re.compile(r"\d+\s*(?:分钟|秒|小时|天|周|个月|年|点)")
MONEY_RE = re.compile(r"\d+\s*(?:万|元|块|k|K|w|W)\b|\d+[wWkK]")
QUOTE_RE = re.compile(r"[「『\"“][^」』\"”]{2,60}[」』\"”]")
SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
CLAUSE_SPLIT = re.compile(r"[，,、。！？!?；;]+")


def body_of(text):
    m = re.search(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", text, re.M | re.S)
    raw = m.group(1) if m else text
    raw = re.sub(r"```.*?```", "", raw, flags=re.S)
    raw = re.sub(r"（正文总字数[^）]*）", "", raw)
    return raw.strip()


def sentences(body):
    return [s.strip() for s in SENT_SPLIT.split(body) if len(s.strip()) >= 2]


def find_parallel(body):
    """段内连续 ≥3 个分句**句式重复** → 排比。决策 5：排比是活人感杀手，全篇最多 1 处。

    只认首 2 字相同这一条。曾经加过「长度差 ≤1」，实测把细节铺陈
    （「我的PPT停在第11页 / 标题写着项目背景 / 投影仪的风扇声一直在响」）
    误判成排比——那恰恰是决策 5 要的「无用的具体细节」，方向正好反了。
    中文短句天然等长，长度不是排比的信号。
    """
    hits = []
    for para in body.split("\n"):
        cl = [c.strip() for c in CLAUSE_SPLIT.split(para) if len(c.strip()) >= 5]
        for i in range(len(cl) - 2):
            w = cl[i:i + 3]
            if len({c[:2] for c in w}) == 1:
                hits.append(" / ".join(w))
    return hits


# ── 可追溯率（2026-08-11 加，T13 基准测试的产出）────────────────────────────
# 起因：拿外部真爆款反测时发现「具体名词密度 ≥2.0」在**奖励编造**。
#   我们按新标准自产的对标稿：密度 1.92（⛔ 未过），可追溯率 21.3%
#   外部爆款（日均 500 赞）：密度 2.39（✅ 通过），可追溯率 **0.0%**
# 那篇爆款全篇零引语，密度靠「10 道题」「覆盖 90% 以上」「背了三天」撑起来 ——
# 而「90% 以上」正是审核员点名的**无来源自我承诺**。
# 也就是说：这个指标分不清「真实的具体细节」和「编出来的数字」，
# 而后者更容易堆。它惩罚了我们最想要的（可追溯原话），奖励了明令禁止的（编造）。
#
# 可追溯率直接数「正文里有多少字能在素材库逐字查到」，编的字符串查不到，刷不了。
# 口径与 independent_audit._pick_by_draft 的强命中一致：6 字连续相同 = 照抄。
#
# ⚠️ 本脚本只测量不判定（见模块 docstring）。是否拿它替换密度做拦截项，
# 属于闸门变更，需 Eric 定 —— 见 docs/20260811-基准测试-外部爆款反测.md 待办 H。
TRACE_N = 6
_LIB_CACHE = None


def _norm_ln(s):
    import unicodedata
    return "".join(ch for ch in (s or "") if unicodedata.category(ch)[0] in "LN")


def _source_ngrams():
    """素材库（评论区原话 + 案例库 + probe quotes）的 6-gram 集合，进程内缓存。"""
    global _LIB_CACHE
    if _LIB_CACHE is not None:
        return _LIB_CACHE
    import csv as _csv, json as _json
    lib = set()

    def add(text):
        b = _norm_ln(text)
        lib.update(b[i:i + TRACE_N] for i in range(len(b) - TRACE_N + 1))

    def rows(p):
        return list(_csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []

    for r in rows(SUCAI / "评论区原话.csv"):
        add((r.get("用户原话") or "") + (r.get("暴露的处境") or ""))
    for r in rows(SUCAI / "案例库.csv"):
        add("".join(r.get(c) or "" for c in ("场景", "对方原话", "我的原话", "可迁移的那一句")))
    for f in (SUCAI / "探测原始").glob("*.result.json"):
        try:
            d = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for q in (d.get("quotes") or []):
            add((q.get("用户原话") or "") + (q.get("暴露的处境") or ""))
    _LIB_CACHE = lib
    return lib


def traceable_rate(body):
    """正文里能在素材库逐字查到的字占比（%）。编造的内容查不到，刷不了这个数。"""
    b = _norm_ln(body)
    if len(b) < TRACE_N:
        return 0.0
    lib = _source_ngrams()
    grams = [b[i:i + TRACE_N] for i in range(len(b) - TRACE_N + 1)]
    return round(sum(1 for g in grams if g in lib) / len(grams) * 100, 1)


def measure(text):
    body = body_of(text)
    clean = re.sub(r"\s", "", body)
    n = len(clean) or 1
    sents = sentences(body)
    lens = [len(re.sub(r"\s", "", s)) for s in sents] or [0]

    concrete = (len(NUM_RE.findall(body)) + len(TIME_RE.findall(body))
                + len(MONEY_RE.findall(body)) + sum(body.count(r) for r in ROLE_WORDS))
    parallel = find_parallel(body)

    return {
        "字数": n,
        "句数": len(sents),
        "平均句长": round(statistics.mean(lens), 1),
        "句长标准差": round(statistics.pstdev(lens), 1) if len(lens) > 1 else 0.0,
        "书面连词/百字": round(sum(body.count(w) for w in FORMAL_CONJ) / n * 100, 2),
        "口语词/百字": round(sum(body.count(w) for w in COLLOQUIAL) / n * 100, 2),
        "具体名词/百字": round(concrete / n * 100, 2),
        "引语数": len(QUOTE_RE.findall(body)),
        "引语/百字": round(len(QUOTE_RE.findall(body)) / n * 100, 2),
        "可追溯率%": traceable_rate(body),
        "排比处数": len(parallel),
        "_排比实例": parallel,
    }


def hook_signals(text):
    body = body_of(text)
    head = re.sub(r"\s", "", body)[:15]
    return {
        "开头含数字": bool(NUM_RE.search(head)),
        "开头含身份": any(r in head for r in ROLE_WORDS),
        "开头含引语": bool(re.match(r"^[「『\"“]", head)),
        "开头15字": head,
    }


DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 书面连词在全部 21 篇实测均为 0，对这批稿无区分度，已从主表移除（函数仍计算，留待新稿观察）
COLS = ["字数", "平均句长", "口语词/百字",
        "具体名词/百字", "引语/百字", "可追溯率%", "排比处数"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--show", help="打印该指标的命中实例（当前支持：排比）")
    args = ap.parse_args()

    files = [SUCAI / args.file] if args.file else sorted(SUCAI.glob("成稿_*.md"))
    if args.show:
        for f in files:
            m = measure(f.read_text(encoding="utf-8"))
            if m["_排比实例"]:
                print(f"\n### {f.name}（{len(m['_排比实例'])} 处）")
                for h in m["_排比实例"][:8]:
                    print("   ", h[:90])
        return 0

    print(f"{'成稿':<30}" + "".join(f"{c:>13}" for c in COLS))
    rows = []
    for f in files:
        m = measure(f.read_text(encoding="utf-8"))
        rows.append((f.name, m))
        d = DATE_RE.search(f.name)
        label = (d.group(1)[5:] if d else f.stem)[:28]
        print(f"{label:<30}" + "".join(f"{m[c]:>13}" for c in COLS))

    if len(rows) > 1:
        print(f"\n{'中位数':<30}" + "".join(
            f"{statistics.median([m[c] for _, m in rows]):>13}" for c in COLS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
