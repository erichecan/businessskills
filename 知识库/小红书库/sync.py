#!/usr/bin/env python3
"""把 xhs/素材库/职场面试_记忆库.csv 中热度过线的笔记增量同步到 notes.jsonl。

去重键：url 优先，归一化标题兜底。重跑安全（append-only）。
用法：python3 知识库/小红书库/sync.py [--min-heat 10000]
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
REPO = LIB_DIR.parent.parent
MEMORY_CSV = REPO / "xhs" / "素材库" / "职场面试_记忆库.csv"
NOTES = LIB_DIR / "notes.jsonl"

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️⁉‼]", flags=re.UNICODE
)
_PUNCT = re.compile(r"[\s　，。！？、,.!?：:；;~～·…—\-（）()【】\[\]\"'“”‘’]")


def normalize_title(title: str) -> str:
    return _PUNCT.sub("", _EMOJI.sub("", title or "")).lower().strip()


def parse_heat(raw: str):
    s = (raw or "").strip()
    if s in ("", "—", "-"):
        return None
    m = re.match(r"^([\d.]+)\s*[万w]", s, flags=re.IGNORECASE)
    if m:
        return round(float(m.group(1)) * 10000)
    m = re.match(r"^([\d,]+)$", s)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-heat", type=int, default=10000)
    args = ap.parse_args()

    if not MEMORY_CSV.exists():
        print(f"记忆库不存在：{MEMORY_CSV}", file=sys.stderr)
        return 1

    seen_urls, seen_titles = set(), set()
    if NOTES.exists():
        for line in NOTES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            note = json.loads(line)
            if note.get("url"):
                seen_urls.add(note["url"])
            seen_titles.add(normalize_title(note["title"]))

    added = skipped_dup = skipped_heat = 0
    with MEMORY_CSV.open(encoding="utf-8") as f, NOTES.open("a", encoding="utf-8") as out:
        for row in csv.DictReader(f):
            title = (row.get("标题") or "").strip()
            if not title:
                continue
            heat = parse_heat(row.get("热度"))
            if heat is None or heat < args.min_heat:
                skipped_heat += 1
                continue
            url = (row.get("URL") or "").strip() or None
            norm = normalize_title(title)
            if (url and url in seen_urls) or norm in seen_titles:
                skipped_dup += 1
                continue
            if url:
                seen_urls.add(url)
            seen_titles.add(norm)
            note = {
                "id": "xhs_" + hashlib.sha1((url or norm).encode()).hexdigest()[:8],
                "title": title,
                "url": url,
                "keyword": (row.get("关键词") or "").strip() or None,
                "source": (row.get("来源") or "").strip() or None,
                "first_seen": (row.get("首次收录日期") or "").strip() or None,
                "heat": heat,
                "heat_raw": (row.get("热度") or "").strip(),
            }
            out.write(json.dumps(note, ensure_ascii=False) + "\n")
            added += 1

    print(f"新增 {added} 条，重复跳过 {skipped_dup}，热度未过线 {skipped_heat}（门槛 {args.min_heat}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
