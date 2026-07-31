#!/usr/bin/env python3
"""
从成稿.md 提取口播脚本纯文字（去掉时间戳标记、加粗符号等）
Usage: python extract_voiceover.py <成稿.md> <output_voiceover.txt>
"""
import sys, re
from pathlib import Path


def extract(md_path: str, output_path: str) -> str:
    text = Path(md_path).read_text(encoding="utf-8")

    # 找 60秒口播脚本 章节（到下一个 ## 或文件末尾）
    m = re.search(r"##\s*60秒口播脚本\s*\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    if not m:
        raise SystemExit(f"❌ 未找到 '60秒口播脚本' 章节，请检查成稿格式：{md_path}")

    raw = m.group(1).strip()

    # 清理：时间戳标记  **[0-5s 钩子]**  或  [0-5s 钩子]
    clean = re.sub(r"\*{1,2}\[[\d\-s秒\s一-鿿]+\]\*{0,2}", "", raw)
    clean = re.sub(r"\[[\d\-s秒\s一-鿿]+\]", "", clean)
    # 去掉 Markdown 加粗/斜体符号
    clean = re.sub(r"\*+", "", clean)
    # 去掉行首 - — > 等列表符号
    clean = re.sub(r"^[\-—>]+\s*", "", clean, flags=re.MULTILINE)
    # 合并多余空行
    clean = re.sub(r"\n{3,}", "\n\n", clean)

    # 把空行替换为短停顿，保留朗读节奏
    lines = [l.strip() for l in clean.splitlines() if l.strip()]
    final = "\n".join(lines)

    Path(output_path).write_text(final, encoding="utf-8")
    print(f"✅ 口播文字已提取（{len(final)} 字）→ {output_path}")
    return final


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python extract_voiceover.py <成稿.md> <output.txt>")
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])
