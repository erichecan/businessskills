"""PoStory's recurring editorial themes and title-driven cover planning."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

EDITORIAL_LINES = {
    "market_proactively": (
        "越难越要主动营销",
        "从一个客流或需求变淡的场景切入。解释同行减少经营曝光时，"
        "真实案例、清楚的服务信息或及时回复为什么可能帮商家进入顾客候选名单。"
        "给一个低成本、可验证的行动。不要把有机会写成必然抢到订单。",
        ("compare", "journey", "steps", "checklist", "scene", "story"),
    ),
    "count_the_funnel": (
        "经营要算账",
        "写清哪个时段多少人经过、进店、成交、买了什么，以及常问的问题；"
        "工程行业改为咨询、合格咨询、上门、报价、签约。线上可以看触达、点击、咨询、预约。"
        "用同一批客人和明确时段找流失环节，再解释该查什么。示例数字必须标明是假设。",
        ("funnel", "journey", "matrix", "compare", "checklist"),
    ),
    "brand_in_every_consultation": (
        "每次给咨询客人讲解，都是一次品牌塑造",
        "从一个真实或明确标注为示意的客户问题切入。对照具体回答，"
        "说明怎样用照片、案例和取舍建立信任。把反复讲的答案交给团队，"
        "也能用于发帖、咨询和报价，让一次讲解服务下一批客户。",
        ("script", "compare", "scene", "story", "steps", "checklist"),
    ),
}

# Only use actions that can plausibly interact with the cover's visual.
COVER_POSES = {
    "compare": ("compare", "inspect", "think"),
    "journey": ("point-right", "explain", "think"),
    "steps": ("explain", "point-right", "inspect"),
    "checklist": ("point-down", "inspect", "review-chart"),
    "scene": ("inspect", "listen", "think"),
    "story": ("think", "listen", "inspect"),
    "funnel": ("review-chart", "think", "explain"),
    "matrix": ("review-chart", "compare", "inspect"),
    "script": ("listen", "explain", "think"),
}

GENERIC_COVER_LABELS = {"场景", "判断", "下一步", "问题", "解决方案", "行动", "经营", "客户"}


def recent_editorial_drafts(drafts_dir: Path, limit: int = 6) -> list[dict]:
    recent = []
    for path in sorted(drafts_dir.glob("*/note.json"), reverse=True):
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(note, dict) and isinstance(note.get("cover"), dict):
            recent.append(note)
        if len(recent) == limit:
            break
    return recent


def editorial_prompt(recent: list[dict]) -> str:
    counts = Counter(str(note.get("editorial_line", "")) for note in recent)
    preferred = sorted(EDITORIAL_LINES, key=lambda key: counts[key])
    rules = "\n".join(
        f"- {key}｜{name}。{rule} 封面图解可选：{'、'.join(kinds)}。"
        for key, (name, rule, kinds) in EDITORIAL_LINES.items()
    )
    return f"""【PoStory 固定故事线】
每篇选一个 editorial_line，从写作任务和有依据的素材出发，贯穿标题、正文和行动。
明确的任务优先；多条都适合时，按最近稿件优先补：{'、'.join(preferred)}。
{rules}
用可辨认的行业场景比较做法和适用条件，让老板自己判断；给出一件他今天能做的事。
行业不限于餐饮，选题开放时优先考虑整屋换顶、工程型园艺和计划性水暖。
封面是标题的第一幕。cover.title 可以缩短，但必须保留标题承诺。
cover.visual.title_anchor 填从 selected_title 和 cover.title 逐字摘出的 2–8 字具体词组，
例如「换顶报价」「进店」「防倒灌」，并把它写进图解标题或标签，实际画出来。
diagram.title 最多 24 字。diagram.items 写图上能看到的对象、步骤或对话，不能只填「场景／判断／下一步」。
比如「换顶报价为什么差两千」用对比图「换顶报价对照／拆旧／基层处理／清运」；
「路过的人多，怎么没人进店」用漏斗「进店漏斗／经过门口／推门进店／完成下单」；
「园艺报价为什么要先讲排水」用对话「先说清排水／业主：钱花在哪／师傅：先看积水位置」。
角色保持同一品牌身份。程序根据图解内容选相符的动作和位置；可以由图解独立做主图。"""


def validate_cover_story(note: dict) -> None:
    line = note.get("editorial_line")
    if not isinstance(line, str) or line not in EDITORIAL_LINES:
        raise ValueError("稿件必须命中 PoStory 的一条固定故事线")
    cover = note["cover"]
    diagram = cover["diagram"]
    if diagram.get("type") not in EDITORIAL_LINES[line][2]:
        raise ValueError("封面图解类型与本篇故事线不匹配")
    visual = cover.get("visual")
    anchor = visual.get("title_anchor") if isinstance(visual, dict) else None
    if not isinstance(anchor, str) or not 2 <= len(anchor) <= 8 or anchor in GENERIC_COVER_LABELS:
        raise ValueError("封面缺少来自标题的具体视觉关键词")
    if any(anchor not in str(title) for title in (note["selected_title"], cover["title"])):
        raise ValueError(f"封面视觉关键词未同时出现在发布标题和封面标题中：{anchor}")
    items = diagram.get("items")
    diagram_title = diagram.get("title")
    if (not isinstance(diagram_title, str) or not diagram_title.strip() or len(diagram_title) > 24
            or not isinstance(items, list) or not 2 <= len(items) <= 3
            or any(not isinstance(item, str) or not item.strip() or len(item) > 12
                   or item.strip() in GENERIC_COVER_LABELS for item in items)
            or len({item.strip() for item in items}) != len(items)):
        raise ValueError("封面图解需要标题及 2–3 个不同的具体短标签（每项最多 12 字）")
    if not any(anchor in label for label in (diagram_title, *items)):
        raise ValueError(f"封面图解没有体现标题里的「{anchor}」")


def plan_cover(note: dict, recent: list[dict], available_poses: set[str]) -> None:
    """Reuse approved assets, rotating among actions relevant to this title's diagram."""
    cover = note["cover"]
    kind = cover["diagram"]["type"]
    visual = cover["visual"]
    recent_visuals = [
        candidate if isinstance(candidate, dict) else {}
        for candidate in (draft["cover"].get("visual") for draft in recent)
    ]
    last_pose = recent_visuals[0].get("mascot_pose") if recent_visuals else None
    counts = Counter(str(visual.get("mascot_pose", "")) for visual in recent_visuals)
    eligible = [pose for pose in COVER_POSES[kind] if pose in available_poses and pose != last_pose]
    first_page_visual = note["pages"][0].get("visual")
    first_page_pose = (
        str(first_page_visual.get("mascot_pose") or "").strip().lower().replace("_", "-")
        if isinstance(first_page_visual, dict) else ""
    )
    eligible = [pose for pose in eligible if pose != first_page_pose]
    pose = min(eligible, key=lambda item: counts[item], default="none")
    # Pointing poses face the diagram. Other actions can switch sides.
    side = "left" if pose in {"point-right", "point-down", "none"} else (
        "right" if not recent_visuals or recent_visuals[0].get("mascot_side") != "right" else "left"
    )
    visual.update(mascot_pose=pose, mascot_side=side)
