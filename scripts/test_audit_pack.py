#!/usr/bin/env python3
"""审核料包筛选的回归测试 —— 核心是「不许把查得到的变成查不到」。

跑法：python3 scripts/test_audit_pack.py

背景：审核 prompt 原先 148k 字，三个整库占 89%。砍成子集能省 60% 成本，
但砍错了后果比省下的钱严重得多 —— 审核员在料包里找不到某句原话，
就会按「编造原话」扣可信度分（红线之一），一篇好稿被判返工甚至归档。

所以第 1 组用例是硬约束：**成稿里照抄的原话，必须 100% 出现在筛后料包里。**
"""
import csv
import sys
import unicodedata
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
SUCAI = REPO / "xhs" / "素材库"
sys.path.insert(0, str(SCRIPTS / "xhs-health"))

from independent_audit import (CASE_BUDGET, QUOTE_BUDGET, STRONG_N,  # noqa: E402
                               _norm, _ngrams, relevant_cases, relevant_quotes)

failures = []


def check(label, got, want):
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}\n     期望 {want!r}，实际 {got!r}")
        failures.append(label)


def check_true(label, cond, detail=""):
    check(label + (f" {detail}" if detail else ""), bool(cond), True)


def _rows(name):
    with (SUCAI / name).open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


DRAFTS = sorted(SUCAI.glob("成稿_*.md"), reverse=True)[:5]
if not DRAFTS:
    print("⛔ 找不到任何成稿，无法测试")
    sys.exit(1)

print(f"── 1. 硬约束：照抄的原话必须 100% 留在料包里（{len(DRAFTS)} 篇真实成稿）──")
for draft in DRAFTS:
    text = draft.read_text(encoding="utf-8")
    ntext = _norm(text)
    g = _ngrams(ntext, STRONG_N)

    for libname, cols, fn in [
        ("评论区原话.csv", ["用户原话", "暴露的处境"], relevant_quotes),
        ("案例库.csv", ["场景", "对方原话", "我的原话", "可迁移的那一句"], relevant_cases),
    ]:
        rows = _rows(libname)
        # 真值：库里凡与成稿有 >=STRONG_N 字连续公共子串的行，都是「成稿照抄了它」
        expected = {i for i, r in enumerate(rows)
                    if _ngrams(_norm("".join(r.get(c) or "" for c in cols)), STRONG_N) & g}
        block, kept, total, n_strong = fn(text, "")
        nblock = _norm(block)
        # 逐行核对：强命中的每一行，其正文必须能在筛后料包里找到
        missing = [i for i in expected
                   if not (_ngrams(_norm("".join(rows[i].get(c) or "" for c in cols)),
                                   STRONG_N) & _ngrams(nblock, STRONG_N))]
        check(f"{draft.name[:28]}… {libname[:6]} 强命中 {len(expected)} 行全部保留",
              missing, [])

print("\n── 2. 预算与规模 ──")
for draft in DRAFTS[:2]:
    text = draft.read_text(encoding="utf-8")
    qblock, qkept, qtotal, _ = relevant_quotes(text, "")
    cblock, ckept, ctotal, _ = relevant_cases(text, "")
    check_true(f"{draft.name[:24]}… 原话行数 {qkept}<={QUOTE_BUDGET}", qkept <= QUOTE_BUDGET)
    check_true(f"{draft.name[:24]}… 案例行数 {ckept}<={CASE_BUDGET}", ckept <= CASE_BUDGET)
    check_true(f"{draft.name[:24]}… 原话块比整库小", len(qblock) < (SUCAI / "评论区原话.csv").stat().st_size)

print("\n── 3. 强命中超预算时，预算让位（宁可多给，不可漏给）──")
# 构造一篇「把整个原话库都抄进去」的假成稿：强命中数会远超 QUOTE_BUDGET
rows = _rows("评论区原话.csv")
fake = "\n".join((r.get("用户原话") or "") for r in rows)
block, kept, total, n_strong = relevant_quotes(fake, "")
check_true(f"强命中 {n_strong} 行 > 预算 {QUOTE_BUDGET} 时全部保留（实留 {kept}）",
           kept >= n_strong and n_strong > QUOTE_BUDGET)

print("\n── 4. 边界 ──")
for label, arg in [("空成稿", ""), ("无中文成稿", "aaa bbb 123"), ("超短成稿", "面试")]:
    try:
        b, k, t, s = relevant_quotes(arg, "")
        check_true(f"{label} 不崩溃（留 {k} 行）", isinstance(b, str) and k <= QUOTE_BUDGET)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {label} 抛异常：{type(e).__name__}: {e}")
        failures.append(label)

print()
if failures:
    print(f"⛔ {len(failures)} 条未通过")
    sys.exit(1)
print("✅ 全部通过")
