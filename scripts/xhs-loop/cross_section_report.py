#!/usr/bin/env python3
"""
横向对比宽表生成脚本。

拼接四个数据源：
  1. xhs/素材库/审核记录.csv       —— 单篇审核打分
  2. xhs/素材库/成稿_*.md          —— 成稿文本（口径/角度/关键词/首选标题）
  3. xhs/素材库/图文_*_cards.json  —— 图文卡片结构
  4. xhs/素材库/发布数据.csv       —— 实际发布后的观看/互动数据

输出：xhs/素材库/横向对比宽表.csv

用法：
  python3 scripts/xhs-loop/cross_section_report.py            # 生成宽表
  python3 scripts/xhs-loop/cross_section_report.py --dump-titles
      # 只打印每个"处置=发布"成稿文件提取到的【首选】标题，供人工做标题模式标注

标题模式（TITLE_PATTERN_OVERRIDES）需要人工判断，机器无法可靠分类，
分类体系见 xhs/素材库/知识框架.md 第五节（六种钩子）：
  认知反转/反常识、数字悬念、旁观目击、细节震撼、常识颠覆、身份错位、其他
"""
from __future__ import annotations

import csv
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "xhs" / "素材库"
AUDIT_CSV = BASE / "审核记录.csv"
PUBLISH_CSV = BASE / "发布数据.csv"
SCENE_MAP_CSV = BASE / "场景地图.csv"
OUTPUT_CSV = BASE / "横向对比宽表.csv"

VALID_DISPOSITIONS = {"发布", "机修", "归档", "返工", "待人工", "REVISE·待修改"}

# ---------------------------------------------------------------------------
# 人工标题模式标注表（键 = 成稿文件名）。
# 这是本脚本唯一不能自动生成的部分：标题模式需要人读标题原文按
# 知识框架.md 第五节的六种钩子分类判断，脚本只负责把结果拼进宽表。
# 缺失的键会在 dump-titles 模式里被列出来提醒补全。
# ---------------------------------------------------------------------------
TITLE_PATTERN_OVERRIDES: dict[str, str] = {}


def load_title_pattern_overrides(path: Path) -> None:
    """从可选的旁路 CSV（成稿文件,标题模式）加载人工标注，允许脚本复用。"""
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            fn = (row.get("成稿文件") or "").strip()
            pat = (row.get("标题模式") or "").strip()
            if fn and pat:
                TITLE_PATTERN_OVERRIDES[fn] = pat


# ---------------------------------------------------------------------------
# 1. 审核记录.csv：每个成稿文件取「处置=发布」且日期最新的一行
# ---------------------------------------------------------------------------

def load_audit_records() -> dict[str, dict]:
    rows = list(csv.DictReader(AUDIT_CSV.open(encoding="utf-8-sig", newline="")))
    fixed = 0
    for r in rows:
        disp = (r.get("处置") or "").strip()
        if disp not in VALID_DISPOSITIONS:
            # 已知异常：某些行发生列错位，真正的处置结果落在了"审核方"列里
            # （例：成稿_2026-08-20_口头offer没下文.md 那一行）。
            alt = (r.get("审核方") or "").strip()
            if alt in VALID_DISPOSITIONS:
                r["处置"] = alt
                fixed += 1
    if fixed:
        print(f"[audit] 修正了 {fixed} 行列错位（处置/审核方对调）", file=sys.stderr)

    by_file: dict[str, list[dict]] = {}
    for r in rows:
        fn = (r.get("成稿文件") or "").strip()
        if not fn:
            continue
        if (r.get("处置") or "").strip() != "发布":
            continue
        by_file.setdefault(fn, []).append(r)

    best: dict[str, dict] = {}
    for fn, cand in by_file.items():
        cand.sort(key=lambda r: (r.get("日期") or ""))
        best[fn] = cand[-1]  # 日期最新
    return best


# ---------------------------------------------------------------------------
# 2. 成稿_*.md 解析
# ---------------------------------------------------------------------------

BOLD_RE = re.compile(r"\*\*")
LEADING_MARK_RE = re.compile(r"^[\s>]*[①②③④⑤⑥⑦⑧⑨⑩]?\s*\d*[\.\、\)]?\s*")
SHOUXUAN_BRACKET_RE = re.compile(r"[【\[][^】\]]*首选[^】\]]*[】\]]")
TRAILING_PAREN_RE = re.compile(r"[（(][^）)]*[）)]\s*$")


def _clean_line(line: str) -> str:
    return BOLD_RE.sub("", line).strip()


def _strip_leading_marker(text: str) -> str:
    return LEADING_MARK_RE.sub("", text, count=1).strip()


def extract_headline(text: str) -> str | None:
    """从"发布标题"小节里提取带【首选】标记的标题原文。"""
    lines = text.split("\n")
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^#{1,3}\s*发布标题", line.strip()):
            start = i + 1
            continue
        if start is not None and re.match(r"^#{1,3}\s", line.strip()):
            end = i
            break
    if start is None:
        return None
    section = lines[start:end]

    for idx, raw in enumerate(section):
        if "首选" not in raw:
            continue
        line = _clean_line(raw)
        line = SHOUXUAN_BRACKET_RE.sub("", line)
        line = _strip_leading_marker(line)
        line = line.strip()
        if line:
            line = TRAILING_PAREN_RE.sub("", line).strip()
            return line if line else None
        # 标记单独占一行（如 "**① 【首选：xxx】**"），标题在下一行
        for j in range(idx + 1, len(section)):
            cand = section[j].strip()
            if not cand:
                continue
            if cand.startswith(">") or cand.startswith("---"):
                break
            cand = _clean_line(cand)
            cand = _strip_leading_marker(cand)
            cand = TRAILING_PAREN_RE.sub("", cand).strip()
            if cand:
                return cand
        return None
    return None


def extract_field(text: str, label: str) -> str | None:
    """提取形如 "> 口径：xxx" 的头部字段（只在首个 --- 分隔符之前找）。"""
    head = text.split("\n---\n", 1)[0]
    m = re.search(rf"^>\s*{re.escape(label)}[：:]\s*(.+)$", head, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def infer_kouzi(text: str, audit_kouzi: str | None) -> str:
    v = extract_field(text, "口径")
    if v:
        return v
    head = text.split("\n---\n", 1)[0]
    for kz in ("祝福流", "搜索流", "推荐流"):
        if f"{kz}格式" in head or f"{kz}：" in head:
            return kz
    return audit_kouzi or ""


def parse_md(path: Path, audit_kouzi: str | None) -> dict:
    text = path.read_text(encoding="utf-8")
    kouzi = infer_kouzi(text, audit_kouzi)
    angle = extract_field(text, "角度") or "未声明"
    keywords = extract_field(text, "关键词")
    if not keywords:
        m = re.search(r"^>\s*关键词来源[：:]\s*(.+)$", text.split("\n---\n", 1)[0], re.MULTILINE)
        keywords = m.group(1).strip() if m else ""
    headline = extract_headline(text)
    return {
        "口径": kouzi,
        "角度声明": "未声明" if angle == "未声明" else "已声明",
        "角度原文": angle,
        "关键词": keywords or "",
        "首选标题": headline or "",
    }


# ---------------------------------------------------------------------------
# 3. 图文_*_cards.json 解析（文件名精确关联，不做模糊匹配）
# ---------------------------------------------------------------------------

MD_NAME_RE = re.compile(r"^成稿_(\d{4}-\d{2}-\d{2})_(.+)\.md$")


def cards_path_for(md_path: Path) -> Path | None:
    m = MD_NAME_RE.match(md_path.name)
    if not m:
        return None
    date, slug = m.groups()
    return BASE / f"图文_{date}_{slug}_cards.json"


def parse_cards(md_path: Path) -> dict:
    cp = cards_path_for(md_path)
    if cp is None or not cp.exists():
        return {"卡片数": "", "有quote金句": "", "卡片type序列": ""}
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"卡片数": "", "有quote金句": "", "卡片type序列": ""}
    if not isinstance(data, list):
        return {"卡片数": "", "有quote金句": "", "卡片type序列": ""}
    types = [str(c.get("type", "")) for c in data]
    has_quote = any(isinstance(c.get("quote"), str) and c.get("quote").strip() for c in data)
    return {
        "卡片数": len(data),
        "有quote金句": "是" if has_quote else "否",
        "卡片type序列": ",".join(types),
    }


# ---------------------------------------------------------------------------
# 4. 场景地图.csv —— 话题类别匹配
# ---------------------------------------------------------------------------

def load_scene_map() -> list[tuple[str, list[str]]]:
    rows = list(csv.DictReader(SCENE_MAP_CSV.open(encoding="utf-8-sig", newline="")))
    out = []
    for r in rows:
        label = f"{(r.get('阶段') or '').strip()}·{(r.get('场景') or '').strip()}"
        words = [w.strip() for w in (r.get("匹配词") or "").split("|") if w.strip()]
        out.append((label, words))
    return out


def _best_scene_match(text: str, scene_map: list[tuple[str, list[str]]]) -> str | None:
    """在一段文本里找场景匹配词命中最靠前、且并列时最长的那个标签。

    "打断"同时出现在"向上汇报"（匹配词含"打断"）和"晋升答辩"类文本里
    （标题写的是"答辩...被打断"）这种交叉污染很常见，仅按"最长词"排序会让
    通用词（"打断"）压过更靠前、更具体的主场景词（"答辩"），所以按
    (出现位置, -词长) 排序：越靠前、越长的词代表越贴近文本真正在说的主场景。
    """
    best = None  # (position, -len(word), label)
    for label, words in scene_map:
        for w in words:
            if not w:
                continue
            pos = text.find(w)
            if pos == -1:
                continue
            key = (pos, -len(w))
            if best is None or key < (best[0], best[1]):
                best = (pos, -len(w), label)
    return best[2] if best else None


def classify_topic(keywords: str, headline: str, scene_map: list[tuple[str, list[str]]]) -> str:
    """场景匹配优先级：先看关键词字段（成稿头部人工声明，最权威），
    没命中再退到首选标题文本；两处都按 _best_scene_match 的位置优先规则挑词。
    """
    if keywords:
        label = _best_scene_match(keywords, scene_map)
        if label:
            return label
    if headline:
        label = _best_scene_match(headline, scene_map)
        if label:
            return label
    return "未分类"


# ---------------------------------------------------------------------------
# 5. 发布数据.csv —— 按笔记分组，取发布天数最大的快照
# ---------------------------------------------------------------------------

def load_publish_notes() -> list[dict]:
    rows = list(csv.DictReader(PUBLISH_CSV.open(encoding="utf-8-sig", newline="")))
    groups: dict[str, dict] = {}  # key -> {"id":..., "titles": set(), "rows": [...]}
    title_to_key: dict[str, str] = {}

    def key_for(note_id: str, title: str) -> str:
        if note_id:
            return f"id:{note_id}"
        if title in title_to_key:
            return title_to_key[title]
        k = f"title:{title}"
        title_to_key[title] = k
        return k

    for r in rows:
        note_id = (r.get("笔记ID") or "").strip()
        title = (r.get("标题") or "").strip()
        k = key_for(note_id, title)
        g = groups.setdefault(k, {"ids": set(), "titles": set(), "rows": []})
        if note_id:
            g["ids"].add(note_id)
        if title:
            g["titles"].add(title)
            title_to_key.setdefault(title, k)
        g["rows"].append(r)

    # 二次合并：同一 id 下不同抓取批次里，有的行标题空、有的行有标题都已在同一组；
    # 但存在"先无 id 后有 id"或反过来的情况，此处按 id 再合并一次。
    merged: dict[str, dict] = {}
    id_owner: dict[str, str] = {}
    for k, g in groups.items():
        owner_key = None
        for i in g["ids"]:
            if i in id_owner:
                owner_key = id_owner[i]
                break
        if owner_key is None:
            owner_key = k
            for i in g["ids"]:
                id_owner[i] = owner_key
        dst = merged.setdefault(owner_key, {"ids": set(), "titles": set(), "rows": []})
        dst["ids"] |= g["ids"]
        dst["titles"] |= g["titles"]
        dst["rows"].extend(g["rows"])

    notes = []
    for k, g in merged.items():
        def days(row):
            try:
                return int(row.get("发布天数") or -1)
            except ValueError:
                return -1
        final_row = max(g["rows"], key=days)
        title = (final_row.get("标题") or "").strip()
        if not title:
            for t in g["titles"]:
                title = t
                break
        notes.append({
            "key": k,
            "ids": g["ids"],
            "titles": g["titles"],
            "final_row": final_row,
            "display_title": title,
        })
    return notes


# ---------------------------------------------------------------------------
# 匹配：成稿文件（首选标题 / 文件名日期+slug） <-> 发布数据笔记
# ---------------------------------------------------------------------------

def normalize_for_match(s: str) -> str:
    return re.sub(r"[^\w一-鿿]", "", s or "").lower()


def char_jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na or not nb:
        return 0.0
    jac = char_jaccard(na, nb)
    seq = SequenceMatcher(None, na, nb).ratio()
    return max(jac, seq)


def slug_from_filename(md_path: Path) -> tuple[str, str] | None:
    m = MD_NAME_RE.match(md_path.name)
    if not m:
        return None
    return m.group(1), m.group(2)  # date, slug


def date_within(note_date_str: str, md_date: str, days: int = 2) -> bool:
    import datetime
    try:
        note_date = datetime.date.fromisoformat(note_date_str.split(" ")[0])
        base_date = datetime.date.fromisoformat(md_date)
    except ValueError:
        return False
    return abs((note_date - base_date).days) <= days


def match_files_to_notes(md_infos: list[dict], notes: list[dict]) -> dict[str, dict]:
    """返回 {成稿文件名: note}；实现一篇发布笔记只匹配一个成稿文件。

    经验证（见 docs 里的验收说明）：这批数据里但凡是真匹配，字符归一化后的标题
    相似度都精确等于 1.0（作者从未在"首选候选标题"与实际发布标题之间做二次编辑）；
    0.3～0.6 区间的分数无一例外是同主题不同篇之间的误撞（例如"汇报被打断"系列
    十几篇论文式返工，标题结构高度相似但内容各异）。因此策略 1（标题相似度）设高阈值
    只收精确匹配；策略 2（日期+slug）作为兜底，同样收紧到高置信度，宁可漏配也不瞎凑。
    """
    TITLE_THRESHOLD = 0.95
    candidates = []  # (score, md_idx, note_idx, strategy, date_delta)
    for mi, info in enumerate(md_infos):
        headline = info["md"]["首选标题"]
        parsed = slug_from_filename(info["path"])
        md_date = parsed[0] if parsed else None
        for ni, note in enumerate(notes):
            best_title = note["display_title"]
            score = title_similarity(headline, best_title) if headline else 0.0
            if score >= TITLE_THRESHOLD:
                pub_time = (note["final_row"].get("发布时间") or "").strip()
                delta = None
                if md_date and pub_time:
                    try:
                        import datetime
                        ndate = datetime.date.fromisoformat(pub_time.split(" ")[0])
                        fdate = datetime.date.fromisoformat(md_date)
                        delta = abs((ndate - fdate).days)
                    except ValueError:
                        delta = None
                candidates.append((score, mi, ni, "title", delta if delta is not None else 9999))
        if parsed:
            md_date, slug = parsed
            for ni, note in enumerate(notes):
                final_row = note["final_row"]
                pub_time = (final_row.get("发布时间") or "").strip()
                if not pub_time:
                    continue
                if not date_within(pub_time, md_date, days=2):
                    continue
                title = note["display_title"]
                slug_overlap = char_jaccard(normalize_for_match(slug), normalize_for_match(title))
                if slug_overlap >= 0.5:
                    score = 0.9 + slug_overlap * 0.05  # 仅次于精确标题匹配，仍需人工可核验
                    candidates.append((score, mi, ni, "date+slug", 0))

    # 排序：分数降序；同分数按日期差升序（越接近发布当天越优先拿到这条笔记）
    candidates.sort(key=lambda c: (-c[0], c[4]))
    used_md: set[int] = set()
    used_note: set[int] = set()
    result: dict[str, dict] = {}
    strategy_used: dict[str, str] = {}
    for score, mi, ni, strategy, delta in candidates:
        if mi in used_md or ni in used_note:
            continue
        used_md.add(mi)
        used_note.add(ni)
        fn = md_infos[mi]["path"].name
        result[fn] = notes[ni]
        strategy_used[fn] = f"{strategy}:{score:.3f}:delta={delta}"
    result["__strategy__"] = strategy_used  # type: ignore
    return result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

OUTPUT_FIELDS = [
    "成稿文件", "发布标题", "话题类别", "角度是否声明", "口径", "标题模式",
    "总分", "选题", "标题分", "首图", "开头", "正文", "可信度", "CTA",
    "是否用quote金句", "卡片数", "卡片type序列",
    "观看", "点赞", "收藏", "评论", "分享", "搜索来源占比",
    "收藏率", "互动率",
]


def to_float(s: str) -> float | None:
    s = (s or "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_rows(dump_titles: bool = False):
    audit_best = load_audit_records()
    scene_map = load_scene_map()
    notes = load_publish_notes()
    load_title_pattern_overrides(BASE.parent.parent / "scripts" / "xhs-loop" / "title_patterns.csv")

    md_infos = []
    missing_md = []
    for fn, audit_row in audit_best.items():
        path = BASE / fn
        if not path.exists():
            missing_md.append(fn)
            continue
        md_data = parse_md(path, audit_row.get("口径"))
        md_infos.append({"path": path, "audit": audit_row, "md": md_data})

    if dump_titles:
        for info in sorted(md_infos, key=lambda i: i["path"].name):
            print(f"{info['path'].name}\t{info['md']['首选标题']}")
        if missing_md:
            print("\n[缺 md 文件]:", file=sys.stderr)
            for fn in missing_md:
                print(f"  {fn}", file=sys.stderr)
        return [], {}, []

    matches = match_files_to_notes(md_infos, notes)
    strategy_used = matches.pop("__strategy__", {})

    rows = []
    unmatched = []
    for info in md_infos:
        fn = info["path"].name
        note = matches.get(fn)
        if note is None:
            unmatched.append(fn)
            continue
        audit = info["audit"]
        md = info["md"]
        cards = parse_cards(info["path"])
        topic = classify_topic(md["关键词"], md["首选标题"], scene_map)
        final_row = note["final_row"]

        view = to_float(final_row.get("观看"))
        like = to_float(final_row.get("点赞")) or 0
        fav = to_float(final_row.get("收藏")) or 0
        comment = to_float(final_row.get("评论")) or 0
        share = to_float(final_row.get("分享")) or 0

        if view and view > 0:
            fav_rate = round(fav / view, 3)
            engage_rate = round((like + fav + comment + share) / view, 3)
        else:
            fav_rate = ""
            engage_rate = ""

        row = {
            "成稿文件": fn,
            "发布标题": note["display_title"] or md["首选标题"],
            "话题类别": topic,
            "角度是否声明": md["角度声明"],
            "口径": md["口径"],
            "标题模式": TITLE_PATTERN_OVERRIDES.get(fn, ""),
            "总分": audit.get("总分", ""),
            "选题": audit.get("选题", ""),
            "标题分": audit.get("标题", ""),
            "首图": audit.get("首图", ""),
            "开头": audit.get("开头", ""),
            "正文": audit.get("正文", ""),
            "可信度": audit.get("可信度", ""),
            "CTA": audit.get("CTA", ""),
            "是否用quote金句": cards["有quote金句"],
            "卡片数": cards["卡片数"],
            "卡片type序列": cards["卡片type序列"],
            "观看": final_row.get("观看", ""),
            "点赞": final_row.get("点赞", ""),
            "收藏": final_row.get("收藏", ""),
            "评论": final_row.get("评论", ""),
            "分享": final_row.get("分享", ""),
            "搜索来源占比": final_row.get("搜索来源占比", ""),
            "收藏率": fav_rate,
            "互动率": engage_rate,
        }
        rows.append(row)

    return rows, strategy_used, unmatched


def main():
    dump_titles = "--dump-titles" in sys.argv
    rows, strategy_used, unmatched = build_rows(dump_titles=dump_titles)
    if dump_titles:
        return

    rows.sort(key=lambda r: r["成稿文件"])
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"写入 {len(rows)} 行 -> {OUTPUT_CSV}", file=sys.stderr)
    if unmatched:
        print(f"\n未匹配到发布数据的成稿文件（{len(unmatched)} 个）：", file=sys.stderr)
        for fn in unmatched:
            print(f"  {fn}", file=sys.stderr)

    no_pattern = [r["成稿文件"] for r in rows if not r["标题模式"]]
    if no_pattern:
        print(f"\n[提醒] {len(no_pattern)} 行缺少人工标注的「标题模式」，"
              f"请在 scripts/xhs-loop/title_patterns.csv 补充后重跑：", file=sys.stderr)
        for fn in no_pattern:
            print(f"  {fn}", file=sys.stderr)


if __name__ == "__main__":
    main()
