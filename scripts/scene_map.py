#!/usr/bin/env python3
"""场景 × 概念打标 —— 词库 / 成稿共用的那把尺。

## 为什么要有这个模块（2026-08-18）

在此之前系统只认**关键词字符串**：采集出词、probe 验词、pick_topic 挑词，
全程没有「这条词属于哪个场景、考的是七种力里的哪一种」这个维度。后果实测：

- 已验证及以上 139 词里，面试/求职 **76 个（55%）**，绩效面谈 **1 个**、管理场景 2 个
- 「HR问"你的缺点"该怎么回答」有 4 个变体词同时在已验证里，
  而闸门是「同**关键词**最多 2 篇」—— 拦不住同话题换个词串
- 没有任何环节会说「边界力零产出」，所以偏斜可以一路滚下去

这个模块提供的就是那两个缺失的维度。判据全部来自 `场景地图.csv` 和
`概念术语库.json`，**这里不硬编码任何场景或概念名**。

## 最长匹配优先

一条词可能同时命中多个场景：「总经理终面问期望薪资怎么答」既有「总经理」（跨级汇报）
又有「期望薪资」（谈薪）。取**命中词最长**的那个 —— 长词更特异。
所以 `场景地图.csv` 的匹配词里，兜底短词（如「面试」）和特异长词（如「期望薪资」）
可以并存，短词只在没有更长匹配时生效。

⛔ 不要改成「按 CSV 行顺序先到先得」：那要求人工维护一个隐式优先级，
加一行就得重排一次，而重排出错时的表现是「打标悄悄变了」，没人看得见。
"""
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUCAI = REPO / "xhs" / "素材库"
SCENE_CSV = SUCAI / "场景地图.csv"
TERMS_JSON = SUCAI / "概念术语库.json"


def load_scenes() -> list[dict]:
    with SCENE_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_terms() -> list[str]:
    d = json.loads(TERMS_JSON.read_text(encoding="utf-8"))
    return [c["name"] for c in d["concepts"]]


def term_defs() -> list[dict]:
    return json.loads(TERMS_JSON.read_text(encoding="utf-8"))["concepts"]


def tag(text: str, scenes: list[dict] | None = None) -> dict | None:
    """给一段文本（关键词 / 标题 / 成稿正文）打标。命中不到返回 None。

    返回 {阶段, 场景, 概念, 命中词}。概念沿用场景的默认概念 ——
    单条关键词的信息量不足以推翻场景的概念归属，要改由写稿时按实际角度覆盖。
    """
    scenes = scenes if scenes is not None else load_scenes()
    low = text.lower()
    best, best_len = None, 0
    for r in scenes:
        for w in r["匹配词"].split("|"):
            w = w.strip()
            if not w or w.lower() not in low:
                continue
            # 同长度时按场景名字典序定胜负 —— 规则任意但**确定**。
            # 靠遍历顺序（= CSV 行序）的话，往表里插一行就可能悄悄改掉一批词的归属。
            if len(w) > best_len or (len(w) == best_len and best and r["场景"] < best[0]["场景"]):
                best, best_len = (r, w), len(w)
    if not best:
        return None
    r, w = best
    return {"阶段": r["阶段"], "场景": r["场景"], "概念": r["默认概念"], "命中词": w}


def ties(text: str, scenes: list[dict] | None = None) -> list[tuple]:
    """列出同长度并列命中的场景 —— tie 说明匹配词不够特异，该去消歧。"""
    scenes = scenes if scenes is not None else load_scenes()
    low = text.lower()
    hits = [(len(w.strip()), r["场景"], w.strip()) for r in scenes
            for w in r["匹配词"].split("|") if w.strip() and w.strip().lower() in low]
    if not hits:
        return []
    top = max(h[0] for h in hits)
    win = sorted({(h[1], h[2]) for h in hits if h[0] == top})
    return win if len(win) > 1 else []


def scene_of(name: str, scenes: list[dict] | None = None) -> dict | None:
    """按场景名精确取一行 —— 给 pick_topic / brief 反查用。"""
    for r in scenes if scenes is not None else load_scenes():
        if r["场景"] == name:
            return r
    return None


DRAFT_KW_RE = re.compile(r"关键词来源：`?词库\.csv`?「([^」]+)」")


def draft_keyword(text: str) -> str:
    """从成稿头部取它声明的关键词。取不到返回空串。"""
    m = DRAFT_KW_RE.search(text)
    return m.group(1).strip() if m else ""


def ciku_tags() -> dict:
    """关键词 → {阶段,场景,概念}。成稿标签**从词库派生**，不在成稿里再存一份。

    ⛔ 别改成往 200 多个成稿 md 头部各写一份标签：两处存同一个事实，
    改了词库不改成稿就会对不上，而对不上的表现是「矩阵悄悄算错」，没人看得见。
    成稿要写进头部的只有【系列】—— 那是词库里没有、写稿当下才决定的维度。
    """
    with (SUCAI / "词库.csv").open(encoding="utf-8-sig") as f:
        return {(r.get("关键词") or "").strip():
                {k: (r.get(k) or "").strip() for k in ("阶段", "场景", "概念")}
                for r in csv.DictReader(f) if (r.get("关键词") or "").strip()}


def tag_draft(path: Path, tags: dict | None = None, scenes: list[dict] | None = None) -> dict | None:
    """给一篇成稿打标：先按它声明的关键词查词库，查不到再退回按标题正文匹配。"""
    text = path.read_text(encoding="utf-8")
    kw = draft_keyword(text)
    tags = tags if tags is not None else ciku_tags()
    t = tags.get(kw)
    if t and t.get("场景"):
        return {**t, "关键词": kw, "来源": "词库"}
    t = tag(text[:600], scenes)          # 头部含标题与口径行，够判场景了
    return {**t, "关键词": kw, "来源": "正文匹配"} if t else None


def matrix():
    """七种力 × 场景 产出矩阵 —— S5 的 brief 小节直接用这个。"""
    from collections import Counter
    tags, scenes = ciku_tags(), load_scenes()
    drafts = sorted(SUCAI.glob("成稿_*.md")) + sorted((SUCAI / "归档稿").glob("成稿_*.md"))
    got = [(d, tag_draft(d, tags, scenes)) for d in drafts]
    ok = [t for _, t in got if t]
    by_concept = Counter(c for t in ok for c in t["概念"].split("/"))
    by_scene = Counter(t["场景"] for t in ok)
    return {"总数": len(drafts), "已打标": len(ok), "概念": by_concept, "场景": by_scene,
            "未打标": [d.name for d, t in got if not t]}


# ── CLI ────────────────────────────────────────────────────────────────────

def _stats(rows, label, key="关键词"):
    from collections import Counter
    scenes = load_scenes()
    hit = [(r, tag(r.get(key, ""), scenes)) for r in rows]
    tagged = [t for _, t in hit if t]
    cov = len(tagged) / len(rows) * 100 if rows else 0
    print(f"\n## {label}：{len(rows)} 条，打标 {len(tagged)} 条（{cov:.1f}%）")
    print("   阶段:", dict(Counter(t["阶段"] for t in tagged)))
    c = Counter(x for t in tagged for x in t["概念"].split("/"))
    print("   概念:", dict(c.most_common()))
    zero = [n for n in load_terms() if n not in c]
    if zero:
        print(f"   ⚠️ 零覆盖概念: {' · '.join(zero)}")
    miss = [r.get(key, "") for r, t in hit if not t]
    if miss:
        print(f"   未打标 {len(miss)} 条，前 15：")
        for m in miss[:15]:
            print(f"     · {m}")
    return hit


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", action="store_true", help="只统计覆盖率，不落盘")
    ap.add_argument("--verified-only", action="store_true",
                    help="只看已验证及以上的词（打标必须先保住这批）")
    ap.add_argument("--write", action="store_true", help="把标签写回词库.csv")
    ap.add_argument("--matrix", action="store_true", help="按成稿算七种力 × 场景产出矩阵")
    a = ap.parse_args()

    if a.matrix:
        m = matrix()
        print(f"## 成稿产出矩阵：{m['已打标']}/{m['总数']} 篇已打标")
        print("\n### 七种力")
        for n in load_terms():
            c = m["概念"].get(n, 0)
            print(f"   {n}  {c:>3} 篇  {'█' * min(c, 40)}{'  ⛔ 零产出' if not c else ''}")
        print("\n### 场景（仅列有产出的，按篇数降序）")
        for sc, c in m["场景"].most_common():
            print(f"   {sc:<16} {c:>3}")
        zero = [r["场景"] for r in load_scenes() if r["场景"] not in m["场景"]]
        print(f"\n### 零产出场景 {len(zero)}/{len(load_scenes())}：{' · '.join(zero)}")
        if m["未打标"]:
            print(f"\n未打标成稿 {len(m['未打标'])} 篇：{', '.join(m['未打标'][:5])}")
        return 0

    ciku = SUCAI / "词库.csv"
    with ciku.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        cols = list(csv.DictReader(ciku.open(encoding="utf-8-sig")).fieldnames)
    keep = {"已验证", "已发布", "已出稿", "排队"}
    sub = [r for r in rows if (r.get("状态") or "").strip() in keep]

    if a.stats or not a.write:
        _stats(sub, "已验证及以上")
        if not a.verified_only:
            _stats(rows, "全部词库")
        return 0

    scenes = load_scenes()
    for c in ("阶段", "场景", "概念"):
        if c not in cols:
            cols.append(c)
    n = 0
    for r in rows:
        t = tag(r.get("关键词", ""), scenes)
        if t:
            r["阶段"], r["场景"], r["概念"] = t["阶段"], t["场景"], t["概念"]
            n += 1
        else:
            r.setdefault("阶段", ""), r.setdefault("场景", ""), r.setdefault("概念", "")
    tmp = ciku.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    tmp.replace(ciku)
    print(f"✅ 词库已打标 {n}/{len(rows)} 条，写回 {ciku.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
