"""Render a structured PoStory Xiaohongshu note as publishable PNG cards."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1242, 1660
INK = "#20202A"
MUTED = "#77737B"
PAPER = "#FFFCF8"
PANEL = "#FFFFFF"
PINK = "#EC008C"
ORANGE = "#FF7A00"
PALE_PINK = "#FFF0F6"
PALE_ORANGE = "#FFF3E7"
LINE = "#EEE7E1"
REPO_ROOT = Path(__file__).resolve().parents[2]
MASCOT_LIBRARY = REPO_ROOT / "assets" / "postory-xhs" / "mascot" / "postory-guide.png"
MASCOT_POSES = REPO_ROOT / "assets" / "postory-xhs" / "mascot" / "poses"


def _font_path() -> str:
    candidates = [
        os.environ.get("POSTORY_XHS_FONT", ""),
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("找不到支持中文的字体；请设置 POSTORY_XHS_FONT 指向可用字体文件。")


FONT_PATH = _font_path()


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size=size)


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return int(draw.textbbox((0, 0), text, font=font)[2])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        line = ""
        for char in paragraph:
            candidate = line + char
            if line and _measure(draw, candidate, font) > max_width:
                lines.append(line)
                line = char
            else:
                line = candidate
        if line:
            lines.append(line)
    return lines


def _draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                  font: ImageFont.FreeTypeFont, fill: str, max_width: int,
                  line_gap: int = 16, max_lines: int | None = None) -> tuple[int, list[str]]:
    x, y = xy
    lines = _wrap(draw, text, font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        raise ValueError(f"卡片文案超出排版上限（{len(lines)} 行 > {max_lines} 行）：{text[:60]}")
    line_height = int(font.size * 1.32)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + line_gap
    return y, lines


def _gradient(size: tuple[int, int], top: tuple[int, int, int],
              bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line((0, y, size[0], y), fill=color)
    return image


def _draw_brand(draw: ImageDraw.ImageDraw, *, page: int, total: int) -> None:
    draw.rounded_rectangle((82, 1506, 1160, 1577), radius=25, fill="#FFFFFF", outline=LINE, width=2)
    draw.text((112, 1522), "PoStory  ·  北美小店经营笔记", font=_font(28), fill=INK)
    page_text = f"{page:02d} / {total:02d}"
    box = draw.textbbox((0, 0), page_text, font=_font(25))
    draw.text((1118 - (box[2] - box[0]), 1525), page_text, font=_font(25), fill=MUTED)


def _place_illustration(image: Image.Image, relative_path: str | None,
                        asset_root: Path, box: tuple[int, int, int, int]) -> None:
    if not relative_path:
        return
    path = (asset_root / relative_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图卡插画不存在：{path}")
    art = Image.open(path).convert("RGBA")
    art.thumbnail((box[2], box[3]), Image.Resampling.LANCZOS)
    x = box[0] + (box[2] - art.width) // 2
    y = box[1] + (box[3] - art.height) // 2
    image.paste(art, (x, y), art)


def _place_mascot(image: Image.Image, asset_root: Path,
                  box: tuple[int, int, int, int], pose: str = "point-right") -> None:
    pose = pose.strip().lower().replace("_", "-")
    packaged_pose = asset_root / "mascot" / "poses" / f"{pose}.png"
    library_pose = MASCOT_POSES / f"{pose}.png"
    packaged = asset_root / "mascot" / "postory-guide.png"
    path = packaged_pose if packaged_pose.is_file() else library_pose
    if not path.is_file() and pose == "point-right":
        path = packaged if packaged.is_file() else MASCOT_LIBRARY
    if not path.is_file():
        raise FileNotFoundError(f"PoStory 引导形象姿态不存在：{path}")
    mascot = Image.open(path).convert("RGBA")
    mascot.thumbnail((box[2], box[3]), Image.Resampling.LANCZOS)
    x = box[0] + (box[2] - mascot.width) // 2
    y = box[1] + (box[3] - mascot.height) // 2
    image.paste(mascot, (x, y), mascot)


def _draw_category_icon(draw: ImageDraw.ImageDraw, center: tuple[int, int],
                        item: str, color: str, index: int) -> None:
    cx, cy = center
    if "新客" in item or "新客" in item or "顾客" in item:
        draw.ellipse((cx - 8, cy - 12, cx + 8, cy + 4), fill="#FFFFFF")
        draw.arc((cx - 16, cy + 1, cx + 16, cy + 27), 180, 360, fill="#FFFFFF", width=5)
    elif "熟客" in item or "复购" in item:
        draw.ellipse((cx - 15, cy - 7, cx - 3, cy + 5), fill="#FFFFFF")
        draw.ellipse((cx + 3, cy - 7, cx + 15, cy + 5), fill="#FFFFFF")
        draw.arc((cx - 20, cy + 1, cx + 1, cy + 23), 180, 360, fill="#FFFFFF", width=4)
        draw.arc((cx - 1, cy + 1, cx + 20, cy + 23), 180, 360, fill="#FFFFFF", width=4)
    elif any(word in item for word in ("流程", "步骤", "交付", "旅程")):
        for offset in (-13, 0, 13):
            draw.rounded_rectangle((cx + offset - 4, cy - 4, cx + offset + 4, cy + 4),
                                   radius=2, fill="#FFFFFF")
        draw.line((cx - 9, cy, cx + 9, cy), fill="#FFFFFF", width=2)
    elif any(word in item for word in ("风格", "颜色", "款", "样品")):
        for offset, dot_color in ((-9, "#FFFFFF"), (0, "#FFE08A"), (9, "#FFB0D3")):
            draw.ellipse((cx + offset - 4, cy - 4, cx + offset + 4, cy + 4), fill=dot_color)
    elif any(word in item for word in ("提醒", "风险", "注意", "误区")):
        draw.line((cx, cy - 11, cx, cy + 4), fill="#FFFFFF", width=5)
        draw.ellipse((cx - 3, cy + 9, cx + 3, cy + 15), fill="#FFFFFF")
    elif index % 2 == 0:
        draw.line((cx - 13, cy, cx + 13, cy), fill="#FFFFFF", width=4)
        draw.line((cx, cy - 13, cx, cy + 13), fill="#FFFFFF", width=4)
    else:
        draw.polygon([(cx, cy - 15), (cx + 4, cy - 4), (cx + 15, cy),
                      (cx + 4, cy + 4), (cx, cy + 15), (cx - 4, cy + 4),
                      (cx - 15, cy), (cx - 4, cy - 4)], fill="#FFFFFF")


def _draw_diagram(draw: ImageDraw.ImageDraw, diagram: dict[str, Any] | None,
                  box: tuple[int, int, int, int], *, fallback_title: str,
                  fallback_items: list[str], default_type: str) -> None:
    x, y, width, height = box
    diagram = diagram if isinstance(diagram, dict) else {}
    title = str(diagram.get("title") or fallback_title).strip()
    items = [str(item).strip() for item in diagram.get("items", []) if str(item).strip()]
    if not items:
        items = [str(item).strip() for item in fallback_items if str(item).strip()]
    kind = str(diagram.get("type") or default_type).strip().lower()
    kind = {"scene": "story", "storyboard": "story", "dialogue": "script",
            "customer-journey": "journey", "process": "steps"}.get(kind, kind)
    max_items = 4 if kind == "matrix" else 3 if kind in {"steps", "journey", "flow", "funnel", "script", "story"} else 4
    label_limit = 28 if kind == "script" else 18
    items = [item[:label_limit] for item in items[:max_items]] or [fallback_title[:18]]
    draw.rounded_rectangle((x, y, x + width, y + height), radius=30,
                           fill=PANEL, outline=LINE, width=2)
    draw.rounded_rectangle((x + 24, y + 22, x + width - 24, y + 77), radius=22,
                           fill=PALE_PINK)
    title_font_size = 28
    while title_font_size > 18 and _measure(draw, title, _font(title_font_size)) > width - 88:
        title_font_size -= 2
    title_font = _font(title_font_size)
    while title and _measure(draw, title, title_font) > width - 88:
        title = title[:-1]
    _draw_wrapped(draw, (x + 44, y + 31), title, title_font, PINK,
                  width - 88, line_gap=1, max_lines=1)

    top = y + 102
    usable_h = height - 124
    if kind == "journey":
        count = min(4, len(items))
        center_y = top + min(104, usable_h // 2)
        left = x + 100
        right = x + width - 100
        step_x = [left + round((right - left) * i / max(1, count - 1)) for i in range(count)]
        draw.line((step_x[0], center_y, step_x[-1], center_y), fill=ORANGE, width=8)
        for i, (cx, item) in enumerate(zip(step_x, items)):
            radius = 28
            draw.ellipse((cx - radius, center_y - radius, cx + radius, center_y + radius),
                         fill=PINK if i % 2 == 0 else ORANGE, outline="#FFFFFF", width=4)
            draw.text((cx - 8, center_y - 16), str(i + 1), font=_font(26), fill="#FFFFFF")
            label_w = min(180, width // count - 14)
            _draw_wrapped(draw, (cx - label_w // 2, center_y + 48), item,
                          _font(22), INK, label_w, line_gap=1, max_lines=2)
        for cx in step_x[:-1]:
            draw.polygon([(cx + 45, center_y), (cx + 29, center_y - 11),
                          (cx + 29, center_y + 11)], fill=ORANGE)
    elif kind in {"steps", "flow"}:
        count = min(3, len(items))
        gap = 20
        card_w = (width - 52 - gap * (count - 1)) // count
        card_h = min(188, usable_h)
        for i, item in enumerate(items[:count]):
            cx = x + 26 + i * (card_w + gap)
            draw.rounded_rectangle((cx, top, cx + card_w, top + card_h), radius=22,
                                   fill=PALE_ORANGE if i % 2 else PALE_PINK,
                                   outline=LINE, width=2)
            draw.ellipse((cx + 16, top + 16, cx + 58, top + 58), fill=PINK if i % 2 == 0 else ORANGE)
            number = str(i + 1)
            draw.text((cx + 29, top + 19), number, font=_font(24), fill="#FFFFFF")
            _draw_wrapped(draw, (cx + 14, top + 76), item, _font(25), INK,
                          card_w - 28, line_gap=2, max_lines=3)
            if i < count - 1:
                mid_y = top + card_h // 2
                ax = cx + card_w + 2
                draw.line((ax, mid_y, ax + gap - 4, mid_y), fill=ORANGE, width=6)
                draw.polygon([(ax + gap - 4, mid_y), (ax + gap - 15, mid_y - 10),
                              (ax + gap - 15, mid_y + 10)], fill=ORANGE)
    elif kind in {"segments", "compare", "groups"}:
        count = min(3, len(items))
        gap = 18
        card_w = (width - 52 - gap * (count - 1)) // count
        card_h = min(190, usable_h)
        fills = (PALE_PINK, PALE_ORANGE, "#F0F5FF")
        for i, item in enumerate(items[:count]):
            cx = x + 26 + i * (card_w + gap)
            draw.rounded_rectangle((cx, top, cx + card_w, top + card_h), radius=22,
                                   fill=fills[i], outline=LINE, width=2)
            draw.ellipse((cx + card_w // 2 - 22, top + 18, cx + card_w // 2 + 22, top + 62),
                         fill=PINK if i == 0 else ORANGE if i == 1 else "#6C91C7")
            _draw_category_icon(draw, (cx + card_w // 2, top + 40), item,
                                PINK if i == 0 else ORANGE if i == 1 else "#6C91C7", i)
            _draw_wrapped(draw, (cx + 12, top + 76), item, _font(24), INK,
                          card_w - 24, line_gap=2, max_lines=3)
    elif kind in {"checklist", "boundary"}:
        row_h = min(58, usable_h // len(items))
        for i, item in enumerate(items[:4]):
            ry = top + i * (row_h + 6)
            draw.ellipse((x + 28, ry + 5, x + 62, ry + 39), fill=PINK if i == 0 else ORANGE)
            draw.line((x + 37, ry + 23, x + 45, ry + 31, x + 56, ry + 15), fill="#FFFFFF", width=4)
            _draw_wrapped(draw, (x + 78, ry + 1), item, _font(24), INK,
                          width - 105, line_gap=1, max_lines=2)
    elif kind == "matrix":
        gap = 16
        cell_w = (width - 52 - gap) // 2
        cell_h = min(124, (usable_h - gap) // 2)
        fills = (PALE_PINK, PALE_ORANGE, "#F0F5FF", "#F3F6EE")
        for i, item in enumerate(items[:4]):
            row, col = divmod(i, 2)
            cx = x + 26 + col * (cell_w + gap)
            cy = top + row * (cell_h + gap)
            draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h), radius=22,
                                   fill=fills[i], outline=LINE, width=2)
            draw.ellipse((cx + 16, cy + 16, cx + 52, cy + 52), fill=PINK if i % 2 == 0 else ORANGE)
            _draw_wrapped(draw, (cx + 66, cy + 19), item, _font(23), INK,
                          cell_w - 80, line_gap=1, max_lines=3)
    elif kind == "funnel":
        center = x + width // 2
        top_y = top + 4
        band_h = min(76, (usable_h - 20) // len(items))
        max_w = width - 72
        for i, item in enumerate(items):
            band_w = max(176, max_w - i * max(42, max_w // (len(items) + 1)))
            left = center - band_w // 2
            y0 = top_y + i * (band_h + 8)
            draw.polygon([(left, y0), (left + band_w, y0),
                          (left + band_w - 20, y0 + band_h), (left + 20, y0 + band_h)],
                         fill=PALE_PINK if i % 2 == 0 else PALE_ORANGE,
                         outline=PINK if i % 2 == 0 else ORANGE)
            text_w = _measure(draw, item, _font(23))
            draw.text((center - text_w // 2, y0 + 20), item, font=_font(23), fill=INK)
    elif kind == "script":
        for i, item in enumerate(items):
            cy = top + i * min(84, usable_h // len(items))
            left = x + 30 + (i % 2) * 72
            right = x + width - 30 - ((i + 1) % 2) * 72
            bubble_fill = PALE_PINK if i % 2 == 0 else PALE_ORANGE
            draw.rounded_rectangle((left, cy, right, cy + 66), radius=22,
                                   fill=bubble_fill, outline=LINE, width=2)
            _draw_wrapped(draw, (left + 18, cy + 12), item, _font(22), INK,
                          right - left - 36, line_gap=0, max_lines=2)
    elif kind == "story":
        gap = 14
        count = min(3, len(items))
        panel_w = (width - 52 - gap * (count - 1)) // count
        panel_h = min(226, usable_h)
        for i, item in enumerate(items[:count]):
            cx = x + 26 + i * (panel_w + gap)
            draw.rounded_rectangle((cx, top + 4, cx + panel_w, top + panel_h), radius=20,
                                   fill="#FFFFFF", outline=LINE, width=2)
            # Minimal shop scene: awning, counter and a changing signal mark.
            draw.rectangle((cx + 26, top + 82, cx + panel_w - 26, top + 136),
                           fill=PALE_ORANGE, outline=ORANGE, width=2)
            draw.line((cx + 24, top + 82, cx + panel_w - 24, top + 82), fill=PINK, width=8)
            draw.rectangle((cx + 44, top + 111, cx + panel_w - 44, top + 154),
                           fill="#FFFFFF", outline=LINE, width=2)
            _draw_wrapped(draw, (cx + 12, top + 170), item, _font(21), INK,
                          panel_w - 24, line_gap=0, max_lines=2)
    else:
        count = min(3, len(items))
        gap = 18
        card_w = (width - 52 - gap * (count - 1)) // count
        for i, item in enumerate(items[:count]):
            cx = x + 26 + i * (card_w + gap)
            draw.rounded_rectangle((cx, top + 8, cx + card_w, top + usable_h - 4),
                                   radius=24, fill=PALE_ORANGE if i % 2 else PALE_PINK,
                                   outline=LINE, width=2)
            draw.rounded_rectangle((cx + card_w // 2 - 24, top + 28, cx + card_w // 2 + 24,
                                    top + 76), radius=12, fill=PINK if i % 2 == 0 else ORANGE)
            _draw_wrapped(draw, (cx + 14, top + 94), item, _font(24), INK,
                          card_w - 28, line_gap=2, max_lines=3)


def _render_cover(note: dict[str, Any], total: int, asset_root: Path) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 22), fill=PINK)
    draw.rectangle((WIDTH // 2, 0, WIDTH, 22), fill=ORANGE)
    draw.ellipse((810, 948, 1420, 1558), fill=PALE_ORANGE)
    draw.ellipse((-190, 990, 245, 1425), fill=PALE_PINK)
    draw.rounded_rectangle((68, 74, 1174, 1442), radius=56, fill="#FFFFFF", outline=LINE, width=2)
    draw.rounded_rectangle((112, 122, 500, 188), radius=28, fill=PALE_PINK)
    label = str(note.get("label", "小店经营观察"))
    draw.text((143, 135), label[:16], font=_font(30), fill=PINK)
    draw.text((112, 250), "PoStory", font=_font(34), fill=INK)
    draw.rounded_rectangle((112, 315, 194, 326), radius=5, fill=PINK)
    draw.rounded_rectangle((194, 315, 263, 326), radius=5, fill=ORANGE)

    title = str(note.get("title", "")).strip()
    if not title:
        raise ValueError("note.cover.title 不能为空")
    size = 96
    while size > 72:
        font = _font(size)
        lines = _wrap(draw, title, font, 980)
        if len(lines) <= 4:
            break
        size -= 4
    if len(lines) > 4:
        raise ValueError("封面标题太长；请压缩到最多 4 行，避免缩小到手机难读。")
    y, _ = _draw_wrapped(draw, (112, 370), title, font, INK, 980,
                         line_gap=14, max_lines=4)
    subtitle = str(note.get("subtitle", "")).strip()
    if subtitle:
        y += 34
        y, _ = _draw_wrapped(draw, (116, y), subtitle, _font(42), MUTED, 930,
                             line_gap=12, max_lines=3)

    art_y = max(760, y + 24)
    if art_y > 930:
        raise ValueError("封面标题或副标题过长；请缩短后再排入固定插画封面")
    visual = note.get("visual") if isinstance(note.get("visual"), dict) else {}
    cover_pose = str(visual.get("mascot_pose") or "none")
    if cover_pose.lower() not in {"none", "off", "false"}:
        figure_right = visual.get("mascot_side") == "right"
        figure_x = 794 if figure_right else 104
        _place_mascot(image, asset_root, (figure_x, art_y, 340, 1350 - art_y), cover_pose)
        diagram_box = (112 if figure_right else 478, art_y + 34, 650, 360)
    else:
        diagram_box = (112, art_y + 24, 1018, 390)
    _draw_diagram(draw, note.get("diagram"), diagram_box,
                  fallback_title="从店主问题开始", fallback_items=["场景", "判断", "下一步"],
                  default_type="journey")
    draw.rounded_rectangle((112, 1367, 530, 1422), radius=26, fill=PALE_PINK)
    draw.text((140, 1378), "北美小店经营笔记", font=_font(26), fill=PINK)
    _draw_brand(draw, page=1, total=total)
    return image


def _render_page(page: dict[str, Any], page_no: int, total: int,
                 asset_root: Path) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 20, HEIGHT), fill=PINK)
    draw.rectangle((20, 0, 31, HEIGHT), fill=ORANGE)
    label = str(page.get("label", page.get("kind", "经营拆解")))
    draw.rounded_rectangle((82, 82, 525, 150), radius=28, fill=PALE_PINK)
    draw.text((112, 95), label[:20], font=_font(29), fill=PINK)
    draw.ellipse((1085, 77, 1158, 150), fill=PALE_ORANGE)
    draw.text((1101, 94), f"{page_no:02d}", font=_font(26), fill=ORANGE)

    title = str(page.get("title", "")).strip()
    if not title:
        raise ValueError(f"第 {page_no} 页缺少 title")
    font_size = 78
    while font_size >= 58:
        title_font = _font(font_size)
        title_lines = _wrap(draw, title, title_font, 1010)
        if len(title_lines) <= 3:
            break
        font_size -= 4
    if len(title_lines) > 3:
        raise ValueError(f"第 {page_no} 页标题太长；请改成最多 3 行：{title[:50]}")
    y, _ = _draw_wrapped(draw, (94, 213), title, title_font, INK, 1010,
                         line_gap=10, max_lines=3)
    y += 24
    draw.rounded_rectangle((94, y, 1148, y + 3), radius=2, fill=LINE)
    y += 48

    body = page.get("body", "")
    points = page.get("points", [])
    if isinstance(body, list):
        paragraphs = [str(x) for x in body if str(x).strip()]
    else:
        paragraphs = [x.strip() for x in str(body).split("\n") if x.strip()]
    if points:
        paragraphs.extend([f"•  {str(item)}" for item in points])
    if not paragraphs:
        raise ValueError(f"第 {page_no} 页需要 body 或 points")

    # Use a roomy editorial panel; each paragraph stays readable on a phone.
    panel_top = y
    callout = str(page.get("callout", "")).strip()
    footnote = str(page.get("footnote", "")).strip()
    body_font = _font(34)
    paragraph_gap = 18
    content_bottom = 760
    for paragraph in paragraphs:
        if paragraph.startswith("•"):
            px, fill = 130, INK
        else:
            px, fill = 112, INK
        y, lines = _draw_wrapped(draw, (px, y), paragraph, body_font, fill,
                                 1000 - (px - 112), line_gap=7)
        y += paragraph_gap
        if y > content_bottom:
            raise ValueError(f"第 {page_no} 页正文太长；拆分到下一张卡或缩短。")
    panel_bottom = min(1450, max(y + 30, panel_top + 180))
    draw.rounded_rectangle((78, panel_top - 23, 1164, panel_bottom), radius=34,
                           fill=PANEL, outline=LINE, width=2)
    # Repaint text after the panel so it remains on top.
    y = panel_top
    for paragraph in paragraphs:
        px = 130 if paragraph.startswith("•") else 112
        y, _ = _draw_wrapped(draw, (px, y), paragraph, body_font, INK,
                             1000 - (px - 112), line_gap=7)
        y += paragraph_gap

    visual = page.get("visual") if isinstance(page.get("visual"), dict) else {}
    pose = str(visual.get("mascot_pose") or "").strip()
    show_mascot = pose and pose.lower() not in {"none", "off", "false"}
    fallback_items = [str(item) for item in points] if points else paragraphs[:3]
    default_diagram = {
        "scene": "story", "insight": "segments", "steps": "steps",
        "comparison": "compare", "checklist": "checklist", "boundary": "boundary",
        "metric": "steps",
    }.get(str(page.get("kind", "")), "steps")
    if show_mascot:
        mascot_side = str(visual.get("mascot_side") or "left")
        if mascot_side == "right":
            draw.ellipse((822, 805, 1150, 1240), fill=PALE_PINK)
            _place_mascot(image, asset_root, (824, 790, 328, 440), pose)
            diagram_box = (94, 830, 704, 382)
        else:
            draw.ellipse((94, 805, 420, 1240), fill=PALE_PINK)
            _place_mascot(image, asset_root, (96, 790, 340, 440), pose)
            diagram_box = (448, 830, 704, 382)
    else:
        diagram_box = (94, 830, 1054, 382)
    _draw_diagram(draw, page.get("diagram"), diagram_box,
                  fallback_title=title, fallback_items=fallback_items,
                  default_type=default_diagram)

    if callout:
        draw.rounded_rectangle((94, 1280, 1148, 1437), radius=28, fill=PALE_ORANGE)
        call_font = _font(35)
        _draw_wrapped(draw, (127, 1310), callout, call_font, INK, 990,
                      line_gap=6, max_lines=3)
    if footnote:
        text = footnote[:68]
        draw.text((94, 1464), text, font=_font(21), fill=MUTED)
    _draw_brand(draw, page=page_no, total=total)
    return image


def render_note(note: dict[str, Any], outdir: Path) -> list[Path]:
    cover = note.get("cover")
    pages = note.get("pages")
    if not isinstance(cover, dict) or not isinstance(pages, list) or not pages:
        raise ValueError("笔记结构需要 cover 对象和至少一张 pages 卡片")
    total = len(pages) + 1
    outdir.mkdir(parents=True, exist_ok=True)
    asset_root = outdir.parent
    outputs = [outdir / "01-cover.png"]
    _render_cover({**cover, "title": cover.get("title") or note.get("selected_title")}, total, asset_root).save(outputs[0], optimize=True)
    for idx, page in enumerate(pages, 2):
        out = outdir / f"{idx:02d}-{page.get('kind', 'page')}.png"
        _render_page(page, idx, total, asset_root).save(out, optimize=True)
        outputs.append(out)
    return outputs


def required_mascot_poses(note: dict[str, Any]) -> list[str]:
    """Return only the approved pose assets referenced by this note."""
    poses: set[str] = set()
    cover = note.get("cover") if isinstance(note.get("cover"), dict) else {}
    cover_visual = cover.get("visual") if isinstance(cover.get("visual"), dict) else {}
    cover_pose = str(cover_visual.get("mascot_pose") or "none").strip().lower().replace("_", "-")
    if cover_pose not in {"none", "off", "false"}:
        poses.add(cover_pose)
    for page in note.get("pages", []):
        visual = page.get("visual") if isinstance(page, dict) and isinstance(page.get("visual"), dict) else {}
        pose = str(visual.get("mascot_pose") or "").strip().lower().replace("_", "-")
        if pose and pose not in {"none", "off", "false"}:
            poses.add(pose)
    return sorted(poses)


def main() -> int:
    parser = argparse.ArgumentParser(description="把结构化 PoStory 笔记渲染为竖版 PNG 图卡")
    parser.add_argument("note_json", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    note = json.loads(args.note_json.read_text(encoding="utf-8"))
    for path in render_note(note, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
