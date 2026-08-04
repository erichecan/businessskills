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
COLS = ["字数", "平均句长", "句长标准差", "口语词/百字",
        "具体名词/百字", "引语/百字", "排比处数"]


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
