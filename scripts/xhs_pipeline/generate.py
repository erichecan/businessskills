"""Generate a Postory draft from the selected pipeline's materials."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from shutil import copy2, rmtree
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from codex_cli import run_prompt  # noqa: E402
from xhs_pipeline.runtime import active_context  # noqa: E402
from xhs_pipeline.collect import _matrix_matches  # noqa: E402
from xhs_pipeline.editorial import (  # noqa: E402
    editorial_prompt, plan_cover, recent_editorial_drafts, validate_cover_story,
)
from xhs_pipeline.render_cards import render_note, required_mascot_poses  # noqa: E402

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json"}
MAX_SOURCE_CHARS = 32_000
MAX_PUBLIC_ITEMS = 3
REPO_ROOT = Path(__file__).resolve().parents[2]
POSTORY_SKILLS = (
    "postory-xhs-title", "postory-xhs-body", "postory-xhs-opening",
    "postory-xhs-graphic", "postory-xhs-review",
)
POSTORY_MASCOT_PATH = REPO_ROOT / "assets" / "postory-xhs" / "mascot" / "postory-guide.png"
ALLOWED_MASCOT_POSES = {
    "none", "off", "false", "listen", "think", "review-chart", "caution",
    "celebrate", "welcome", "point-down", "compare", "inspect", "explain", "point-right",
}
ALLOWED_DIAGRAM_TYPES = {
    "scene", "story", "journey", "steps", "segments", "compare", "matrix",
    "funnel", "script", "checklist",
}
TOPIC_ALIASES = {
    "open_shop_investment": ("开店", "选址", "租金", "启动资金", "装修投入", "startup cost", "lease cost"),
    "customer_demand": ("顾客", "回头客", "复购", "会员", "细分", "customer loyalty", "customer retention", "repeat customer", "customer segmentation", "market segmentation", "target marketing"),
    "marketing_metrics": ("营销", "发帖", "点赞", "曝光", "流量", "预约", "线索", "kpi", "marketing", "social media", "conversion"),
    "good_actions": ("管理", "定价", "服务", "跟进", "促销", "销售", "marketing plan", "pricing", "sales follow-up"),
    "business_model": ("商业模式", "收入", "成本", "利润", "客单价", "revenue", "cost", "profit", "business model"),
}


def _brief_topics(brief: str, matrix: dict) -> set[str]:
    topic_ids = set(_matrix_matches(brief, matrix))
    lowered = brief.lower()
    for topic_id, aliases in TOPIC_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            topic_ids.add(topic_id)
    return topic_ids


def read_materials(context, brief: str = "") -> tuple[str, list[str]]:
    chunks: list[str] = []
    used: list[str] = []
    remaining = MAX_SOURCE_CHARS
    configured_sources = [
        source for source in context.generation.get("material_sources", [])
        if source.get("enabled", True)
    ]
    configured_sources.extend(
        {"enabled": True, "path": source.get("settings", {}).get("path"), "id": source.get("id")}
        for source in context.collection.get("sources", [])
        if source.get("enabled") and source.get("settings", {}).get("path")
    )
    default_brand = context.data_root / "materials" / "PoStory品牌事实_启用前核对.md"
    if default_brand.is_file():
        configured_sources.append({"enabled": True, "path": str(default_brand), "id": "postory_brand_facts"})
    seen_files: set[Path] = set()
    for source in configured_sources:
        if not source.get("path"):
            continue
        path = Path(os.path.expandvars(source["path"])).expanduser().resolve()
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
        for file_path in files:
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
            if file_path.suffix.lower() not in TEXT_SUFFIXES or remaining <= 0:
                continue
            try:
                raw = file_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                continue
            if file_path.suffix.lower() == ".csv":
                try:
                    rows = list(csv.reader(raw.splitlines()))
                    raw = "\n".join(" | ".join(row) for row in rows)
                except csv.Error:
                    pass
            raw = raw[:remaining]
            remaining -= len(raw)
            if raw.strip():
                chunks.append(f"\n--- 素材文件：{file_path.name} ---\n{raw}")
                used.append(str(file_path))
    # Public-source collection is the primary PoStory material source. Keep its
    # provenance visible in the prompt and the generated draft metadata.
    collected_dir = context.data_root / "collection" / "items"
    if collected_dir.is_dir() and remaining > 0:
        records = []
        matrix = context.collection.get("editorial_matrix", {})
        requested_topics = _brief_topics(brief, matrix) if brief else set()
        source_settings = {
            source.get("id"): source.get("settings", {})
            for source in context.collection.get("sources", [])
        }
        for file_path in collected_dir.glob("*.json"):
            try:
                record = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records.append((record.get("collected_at", ""), file_path, record))
        records.sort(key=lambda row: row[0], reverse=True)
        candidates = []
        for _, file_path, record in records:
            if remaining <= 0:
                break
            # Short snippets remain useful for discovery/review, but are not
            # strong enough evidence for automatic drafting.
            if record.get("content_status") != "full_text":
                continue
            excluded_title_terms = source_settings.get(record.get("source_id"), {}).get("exclude_title_terms", [])
            title_lower = str(record.get("title", "")).lower()
            if any(str(term).lower() in title_lower for term in excluded_title_terms):
                continue
            evidence_text = f"{record.get('title', '')}\n{record.get('description', '')}\n{(record.get('body') or '')[:5000]}"
            live_matches = set(_matrix_matches(evidence_text, matrix))
            lowered_evidence = evidence_text.lower()
            for topic_id, aliases in TOPIC_ALIASES.items():
                if any(alias.lower() in lowered_evidence for alias in aliases):
                    live_matches.add(topic_id)
            if not live_matches or (requested_topics and not (requested_topics & live_matches)):
                continue
            body = (record.get("body") or "").strip()
            description = (record.get("description") or "").strip()
            if not body and not description:
                continue
            candidates.append((file_path, record, live_matches))

        selected = []
        seen_publishers: set[str] = set()
        uncovered_topics = set(requested_topics)
        for pass_no in (1, 2):
            for file_path, record, live_matches in candidates:
                publisher = str(record.get("source_id", ""))
                if pass_no == 1 and publisher in seen_publishers:
                    continue
                if uncovered_topics and not (uncovered_topics & live_matches):
                    continue
                selected.append((file_path, record, live_matches))
                seen_publishers.add(publisher)
                uncovered_topics -= live_matches
                if len(selected) >= MAX_PUBLIC_ITEMS:
                    break
            if len(selected) >= MAX_PUBLIC_ITEMS:
                break
        if not selected and not requested_topics:
            selected = candidates[:MAX_PUBLIC_ITEMS]

        for file_path, record, live_matches in selected:
            body = (record.get("body") or "").strip()
            description = (record.get("description") or "").strip()
            excerpt = body or description
            excerpt = excerpt[:min(remaining, 12000)]
            rendered = (
                f"来源：{record.get('source_name', record.get('source_id', ''))}\n"
                f"标题：{record.get('title', '')}\n"
                f"发布时间：{record.get('published_at', '')}\n"
                f"原文链接：{record.get('canonical_url', '')}\n"
                f"矩阵主题：{', '.join(sorted(live_matches))}\n"
                f"正文摘录：\n{excerpt}"
            )
            rendered = rendered[:remaining]
            remaining -= len(rendered)
            chunks.append(f"\n--- 公开来源证据：{file_path.name} ---\n{rendered}")
            used.append(str(file_path))
    return "\n".join(chunks), used


def build_prompt(context, brief: str, recent: list[dict] | None = None) -> tuple[str, list[str]]:
    rewrite = context.generation["rewrite"]
    prompt_path = Path(os.path.expandvars(rewrite["prompt_file"])).expanduser().resolve()
    if not prompt_path.is_file():
        raise FileNotFoundError(f"改写规则文件不存在：{prompt_path}")
    style = prompt_path.read_text(encoding="utf-8").strip()
    material_text, material_files = read_materials(context, brief)
    if not material_text:
        raise ValueError("没有找到可读取的文本素材；请在配置页添加品牌/服务资料或启用素材目录。")
    scope = context.collection.get("content_scope", "").strip() or "\n".join(
        f"- {source.get('settings', {}).get('content_scope', '')}"
        for source in context.collection.get("sources", [])
        if source.get("enabled") and source.get("settings", {}).get("content_scope")
    ) or "持续解释中小企业经营者面临的营销问题、应关注的指标和可执行动作。"
    skill_text = []
    for skill_name in POSTORY_SKILLS:
        skill_path = REPO_ROOT / "skills" / skill_name / "SKILL.md"
        if skill_path.is_file():
            skill_text.append(skill_path.read_text(encoding="utf-8").strip())
    skills = "\n\n".join(skill_text)
    prompt = f"""{style}

【PoStory 小红书生产 Skills】
{skills}

【本流水线】PoStory 小红书宣传
【内容范围】
{scope}
【本条内容任务】
{brief.strip()}
{editorial_prompt(recent or [])}
【素材事实】
{material_text}

优先围绕一个中小企业经营者需要判断的问题展开：问题是什么、该看什么证据或指标、可以考虑什么动作。
使用公开来源时标注出处或将来源融入叙述；综合多个来源形成 PoStory 自己的解释，不模仿来源作者的口吻，不大段照抄。
区分事实、案例和观点，保留案例边界。不要编造数据、客户案例、效果、价格或承诺。
请只输出一个合法 JSON 对象，不要 Markdown 代码围栏或额外解释，结构如下：
{{
  "editorial_line": "market_proactively|count_the_funnel|brand_in_every_consultation",
  "title_candidates": [{{"title":"...","angle":"...","promise":"..."}}],
  "selected_title": "...",
  "title_selection_reason": "...",
  "opening": "...",
  "caption": "发布时的正文说明，不含标题和话题标签",
  "hashtags": ["#..."],
  "cover": {{"label":"行业/场景标签","title":"...","subtitle":"...","visual":{{"title_anchor":"标题中的具体词组"}},"diagram":{{"type":"scene|journey|steps|segments|compare|matrix|funnel|script|checklist|story","title":"...","items":["短标签","短标签","短标签"]}}}},
  "pages": [
    {{"kind":"scene|insight|steps|comparison|metric|checklist|boundary","label":"...","title":"...","body":"...","points":["..."],"callout":"...","footnote":"...","visual":{{"mascot_pose":"listen|think|review-chart|caution|celebrate|welcome|point-down|compare|inspect|none","mascot_side":"left|right"}},"diagram":{{"type":"scene|journey|steps|segments|compare|matrix|funnel|script|checklist|story","title":"短图解标题","items":["短标签","短标签","短标签"]}}}}
  ],
  "sources": [{{"publisher":"...","title":"...","published_at":"...","url":"..."}}],
  "review_notes": []
}}

要求：标题候选 3–5 个，使用具体行业场景和店主会说的话术，避免咨询报告腔；正文图卡 4–7 页，每页只讲一个要点。每页都要有视觉承载物，但不要强迫每页放人物；内页仅在角色能帮助讲解、倾听、提醒或展示时指定一个姿态，否则省略 visual.mascot_pose 或设为 none。整篇最多在 2–3 张内页出现角色，不得连续重复同一姿态；封面人物动作和位置由程序安排。根据页面内容在场景、旅程、步骤、分组、对比、矩阵、漏斗、对话、清单、故事板等图形语法中选择，避免整篇重复一种结构。items 每项尽量控制在 12 个汉字内。封面标题以首选标题为基础，可为视觉阅读压缩但不能改变承诺。图形标签必须来自本页真实内容；不要请求生成临时图片。source url 必须逐字来自输入素材，不得编造。完整提供读者需要的信息，不以评论/私信换答案。输出适合加拿大及北美华人店主阅读的自然中文；不要声称已发布或保证爆款。"""
    return prompt, material_files


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("生成结果不是 JSON 对象")
    note = json.loads(text[start:end + 1])
    required = ("title_candidates", "selected_title", "opening", "caption", "hashtags", "cover", "pages", "sources")
    missing = [key for key in required if key not in note]
    if missing:
        raise ValueError(f"生成结果缺少字段：{', '.join(missing)}")
    if not isinstance(note["title_candidates"], list) or not 3 <= len(note["title_candidates"]) <= 5:
        raise ValueError("标题候选应为 3–5 个")
    if not isinstance(note["pages"], list) or not 4 <= len(note["pages"]) <= 7:
        raise ValueError("正文图卡应为 4–7 页")
    if not isinstance(note["cover"], dict) or not isinstance(note["hashtags"], list):
        raise ValueError("cover 或 hashtags 格式错误")
    if not isinstance(note["cover"].get("diagram"), dict):
        raise ValueError("封面缺少内容匹配的图解")
    cover_visual = note["cover"].get("visual") if isinstance(note["cover"].get("visual"), dict) else {}
    cover_pose = str(cover_visual.get("mascot_pose") or "").strip().lower().replace("_", "-")
    if cover_pose and cover_pose not in ALLOWED_MASCOT_POSES:
        raise ValueError(f"封面指定了未知角色姿态：{cover_pose}")
    cover_diagram_type = str(note["cover"]["diagram"].get("type", "")).strip().lower()
    if cover_diagram_type and cover_diagram_type not in ALLOWED_DIAGRAM_TYPES:
        raise ValueError(f"封面指定了未知图形语法：{cover_diagram_type}")
    if not str(note["selected_title"]).strip() or not str(note["caption"]).strip():
        raise ValueError("首选标题和正文说明不能为空")
    note["cover"].setdefault("title", note["selected_title"])
    page_poses: list[str] = []
    page_diagrams: list[str] = []
    for index, page in enumerate(note["pages"], 1):
        if not isinstance(page, dict) or not page.get("title") or not (page.get("body") or page.get("points")):
            raise ValueError(f"第 {index} 张正文图卡缺少标题或正文")
        if not isinstance(page.get("diagram"), dict):
            raise ValueError(f"第 {index} 张正文图卡缺少内容匹配的图解")
        visual = page.get("visual") if isinstance(page.get("visual"), dict) else {}
        pose = str(visual.get("mascot_pose") or "").strip().lower().replace("_", "-")
        if pose and pose not in ALLOWED_MASCOT_POSES:
            raise ValueError(f"第 {index} 张图卡指定了未知角色姿态：{pose}")
        page_poses.append(pose)
        diagram_type = str(page["diagram"].get("type", "")).strip().lower()
        if diagram_type and diagram_type not in ALLOWED_DIAGRAM_TYPES:
            raise ValueError(f"第 {index} 张图卡指定了未知图形语法：{diagram_type}")
        page_diagrams.append(diagram_type)
    if sum(pose not in {"", "none", "off", "false"} for pose in page_poses) > 3:
        raise ValueError("内页最多在 3 张卡片中使用引导角色")
    validate_cover_story(note)
    all_poses = page_poses
    for current, following in zip(all_poses, all_poses[1:]):
        if current not in {"", "none", "off", "false"} and current == following:
            raise ValueError(f"相邻卡片重复使用同一角色姿态：{current}")
    if len(note["pages"]) >= 4 and len({kind for kind in page_diagrams if kind}) < 3:
        raise ValueError("一组笔记的内页至少要使用 3 种不同的图形语法")
    if not str(note.get("title_selection_reason", "")).strip():
        raise ValueError("缺少首选标题的选择理由")
    return note


def generate(brief: str) -> Path:
    context = active_context(required=True)
    drafts_dir = context.data_root / "drafts"
    recent = recent_editorial_drafts(drafts_dir)
    prompt, material_files = build_prompt(context, brief, recent)
    raw = run_prompt(prompt, route="first_draft", timeout=900)
    note = _extract_json(raw)
    # Keep citations tied to URLs actually present in the supplied material.
    allowed_urls = {
        url.rstrip(".,;:，。；：）】")
        for url in re.findall(r"https?://[^\s\"<>]+", prompt)
    }
    for source in note.get("sources", []):
        if not isinstance(source, dict) or source.get("url", "").rstrip(".,;:，。；：）】") not in allowed_urls:
            raise ValueError("来源链接不在本次输入素材中；请移除或改用原文链接")
    drafts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", brief.strip())[:30].strip("-") or "post"
    package_dir = drafts_dir / f"{stamp}-{slug}"
    note.update({
        "pipeline_id": context.pipeline_id,
        "account_id": context.account_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "brief": brief.strip(),
        "prompt_file": context.generation["rewrite"]["prompt_file"],
        "material_files": material_files,
        "status": "needs_human_review",
        "visual_identity": "postory-guide-v1",
        "design_version": "postory-visual-system-v3",
        "deliverables": ["note.json", "caption.txt", "cards/*.png"],
    })
    package_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        library = POSTORY_MASCOT_PATH.parent / "poses"
        available_poses = {
            pose for pose in ALLOWED_MASCOT_POSES if (library / f"{pose}.png").is_file()
        }
        plan_cover(note, recent, available_poses)
        for page in note["pages"]:
            visual = page.get("visual")
            if isinstance(visual, dict):
                pose = str(visual.get("mascot_pose") or "").strip().lower().replace("_", "-")
                visual["mascot_pose"] = pose if pose in available_poses else "none"
        note["mascot_poses"] = required_mascot_poses(note)
        if note["mascot_poses"]:
            pose_dir = package_dir / "mascot" / "poses"
            pose_dir.mkdir(parents=True)
            for pose in note["mascot_poses"]:
                copy2(library / f"{pose}.png", pose_dir / f"{pose}.png")
            note["deliverables"].append("mascot/poses/*.png")
            if POSTORY_MASCOT_PATH.is_file():
                copy2(POSTORY_MASCOT_PATH, pose_dir.parent / POSTORY_MASCOT_PATH.name)
                note["deliverables"].append("mascot/postory-guide.png")
        card_paths = render_note(note, package_dir / "cards")
        note["card_files"] = [str(path.relative_to(package_dir)) for path in card_paths]
        (package_dir / "note.json").write_text(
            json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        caption_text = f"{note['selected_title']}\n\n{note['caption']}\n\n{' '.join(note['hashtags'])}\n"
        (package_dir / "caption.txt").write_text(caption_text, encoding="utf-8")
    except Exception:
        rmtree(package_dir, ignore_errors=True)
        raise
    return package_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="按所选流水线素材生成图文笔记包；PoStory 稿件会自动审稿")
    parser.add_argument("--pipeline", default="postory-xhs")
    parser.add_argument("--brief", action="append", required=True,
                        help="本篇要讲的服务/问题/角度；可重复传入以批量生成")
    args = parser.parse_args()
    os.environ["XHS_PIPELINE_ID"] = args.pipeline
    failures = 0
    for brief in args.brief:
        try:
            path = generate(brief)
        except Exception as exc:
            failures += 1
            print(f"⛔ 草稿未生成（{brief}）：{exc}", file=sys.stderr)
            continue
        if args.pipeline == "postory-xhs":
            try:
                from xhs_pipeline.automation import auto_review_package
                review = auto_review_package(active_context(required=True), path)
            except Exception as exc:
                failures += 1
                print(f"⏸️ 稿件已生成但自动审批未完成（{path}）：{exc}", file=sys.stderr)
                continue
            print(f"✅ PoStory 稿件：{path}；自动审核状态：{review['status']}")
        else:
            print(f"✅ 待人工审核图文笔记包：{path}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
