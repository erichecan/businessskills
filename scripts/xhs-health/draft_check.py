#!/usr/bin/env python3
"""成稿机械及格线检查 — 外部的尺（代码硬核对，不靠模型自报）。

对最近 N 篇 成稿_*.md 逐篇检查：
1. 标题 ≤20 字（逐字数，emoji 不计）
2. 正文 800-1200 字
3. CTA/互动段存在（问句结尾或含"评论区/你呢/你遇到过"）
4. AI 味硬指标：「不是X是Y」句式 ≤2 处
5. 跨篇查重：签名句（我面过300人/上周一个候选人 等）近 5 篇内重复即违规

用法：python3 draft_check.py [--days 2]（被 health_check.py 每日调用）
违规 → 打印明细，退出码 1。
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

SUCAI = Path(__file__).resolve().parents[2] / "xhs" / "素材库"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
SIGNATURES = ["我面过300", "上周一个候选人", "笔都没动", "在表上打了叉", "45分钟"]
CTA_HINTS = ["评论区", "你呢", "你遇到过", "你会怎么", "留言", "？\n", "?\n"]
NOT_BUT = re.compile(r"不是[^，。；\n]{1,15}[，,]?[是而]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def extract(text):
    m = re.search(r"^#+\s*(?:首选)?标题[:：]?\s*(.+)$", text, re.M)
    title = m.group(1).strip() if m else ""
    if not title:
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                title = line
                break
    return title


def drafts_sorted():
    files = []
    for f in SUCAI.glob("成稿_*.md"):
        m = DATE_RE.search(f.name)
        if m:
            files.append((date.fromisoformat(m.group(1)), f))
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()

    all_drafts = drafts_sorted()
    cutoff = date.today() - timedelta(days=args.days)
    recent = [(d, f) for d, f in all_drafts if d >= cutoff]
    if not recent:
        print("近期无成稿，跳过机械检查")
        return 0

    problems = []
    for d, f in recent:
        text = f.read_text(encoding="utf-8")
        body = re.sub(r"```.*?```", "", text, flags=re.S)
        issues = []

        title = extract(text)
        tlen = len(_EMOJI.sub("", title))
        if tlen > 20:
            issues.append(f"标题 {tlen} 字（>20）：「{title[:30]}」")

        bm = re.search(r"^#{1,3}\s*\*{0,2}正文[^\n]*\n(.*?)(?=\n#{1,3}\s|\Z)", text, re.M | re.S)
        if bm:
            blen = len(re.sub(r"\s|（正文总字数[^）]*）", "", bm.group(1)))
            if not 280 <= blen <= 560:
                issues.append(f"正文节 {blen} 字（搜索流规格 300-500）")
        else:
            clen = len(re.sub(r"\s", "", body))
            if not 300 <= clen <= 2000:
                issues.append(f"全文 {clen} 字且未找到正文节")
        if "五问启动检查" in text:
            issues.append("成稿文件包含「五问启动检查」章节（应只打印不落盘）")
        if "正文总字数" in text:
            issues.append("成稿文件包含「正文总字数」标注行（应只打印不落盘）")

        if not any(h in text for h in CTA_HINTS):
            issues.append("未检出 CTA/互动段（无问句结尾、无评论区引导）")

        nb = len(NOT_BUT.findall(body))
        if nb > 2:
            issues.append(f"「不是X是Y」句式 {nb} 处（>2，AI 味硬指标）")

        prev5 = [pf.read_text(encoding="utf-8") for pd, pf in all_drafts if pd < d][-5:]
        for sig in SIGNATURES:
            if sig in text and any(sig in p for p in prev5):
                issues.append(f"签名句「{sig}」近 5 篇内重复使用（模板自我复制）")

        if issues:
            problems.append((f.name, issues))

    if not problems:
        print(f"机械及格线检查通过（{len(recent)} 篇）")
        return 0
    for name, issues in problems:
        print(f"⛔ {name}")
        for i in issues:
            print(f"   - {i}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
