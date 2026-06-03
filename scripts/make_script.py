#!/usr/bin/env python3
"""
make_script.py — 根据话题自动生成口播稿 + 分镜描述

用法:
  python make_script.py --topic "做了父亲之后，我才真正看懂了我爸" --output-dir day1/
  python make_script.py --topic-file day1/topic.txt --output-dir day1/

输出:
  <output-dir>/script.txt    — 口播稿（直接给 make_voice.py 用）
  <output-dir>/storyboard.txt — 分镜描述（直接给 make_images.py 用）
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROMPT_TEMPLATE = """你是一位专注于中年男性情感共鸣的视频号创作者，目标人群是35-50岁的中国男性。

话题：{topic}

请生成以下内容：

【口播稿要求】
- 总时长60-90秒，约200-280字
- 开头5秒必须有强钩子：情感共鸣 / 反常识 / 直击痛点，让人停下来
- 用口语化语言，有停顿感（可以用"……"或句号断句）
- 第一人称或"你"贯穿始终，拉近距离
- 情绪要有层次：钩子→共鸣→反转或洞察→落地行动
- 结尾有具体的行动号召或情感收尾
- 禁止废话、禁止大道理说教

【分镜要求】
- 4到6个场景，每个场景对应口播稿的一个情感节点
- 每个场景用英文描述（给图像生成模型用）
- 场景必须包含角色动作、情绪状态、环境氛围
- 参考角色：中年男性，40岁左右，白衬衫，疲惫沧桑感

严格按以下格式输出，不要输出任何其他内容：

---SCRIPT---
（口播稿内容）
---STORYBOARD---
（场景1英文描述）
（场景2英文描述）
（场景3英文描述）
（场景4英文描述）
"""


def get_openai_client():
    """优先用环境变量 OPENAI_API_KEY，其次读 codex auth.json 的 access_token"""
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ 请安装 openai: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return OpenAI(api_key=api_key)

    auth_path = Path.home() / ".codex" / "auth.json"
    if auth_path.exists():
        try:
            auth = json.loads(auth_path.read_text())
            token = auth.get("tokens", {}).get("access_token")
            if token:
                return OpenAI(api_key=token)
        except Exception:
            pass

    print("❌ 未找到 OpenAI API Key，请设置环境变量 OPENAI_API_KEY 或通过 codex 登录")
    sys.exit(1)


def generate_script(topic: str) -> str:
    client = get_openai_client()
    prompt = PROMPT_TEMPLATE.format(topic=topic)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=1200,
    )
    return response.choices[0].message.content.strip()


def parse_output(raw: str) -> tuple[str, str]:
    if "---SCRIPT---" not in raw or "---STORYBOARD---" not in raw:
        raise ValueError("输出格式不符合预期，缺少 ---SCRIPT--- 或 ---STORYBOARD--- 标记")

    script_part = raw.split("---SCRIPT---")[1].split("---STORYBOARD---")[0].strip()
    storyboard_part = raw.split("---STORYBOARD---")[1].strip()

    scenes = []
    for line in storyboard_part.splitlines():
        line = line.strip().strip("（）()").strip()
        if line:
            scenes.append(line)

    return script_part, "\n".join(scenes)


def main():
    parser = argparse.ArgumentParser(description="根据话题生成口播稿和分镜")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", help="直接输入话题描述")
    group.add_argument("--topic-file", help="从文件读取话题")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    args = parser.parse_args()

    if args.topic_file:
        topic_path = Path(args.topic_file)
        if not topic_path.exists():
            print(f"❌ 找不到话题文件: {args.topic_file}")
            sys.exit(1)
        topic = topic_path.read_text(encoding="utf-8").strip()
    else:
        topic = args.topic.strip()

    if not topic:
        print("❌ 话题为空")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📝 话题：{topic}")
    print(f"🤖 调用 OpenAI API 生成口播稿 + 分镜...")

    try:
        raw = generate_script(topic)
    except Exception as e:
        print(f"❌ API 调用失败：{e}")
        sys.exit(1)

    if not raw:
        print("❌ API 返回内容为空")
        sys.exit(1)

    try:
        script, storyboard = parse_output(raw)
    except ValueError as e:
        print(f"❌ 解析失败：{e}")
        print("--- 原始输出 ---")
        print(raw[:500])
        sys.exit(1)

    script_path = output_dir / "script.txt"
    storyboard_path = output_dir / "storyboard.txt"

    script_path.write_text(script, encoding="utf-8")
    storyboard_path.write_text(storyboard, encoding="utf-8")

    scene_count = len([l for l in storyboard.splitlines() if l.strip()])

    print(f"✅ 口播稿 → {script_path}（{len(script)} 字）")
    print(f"✅ 分镜   → {storyboard_path}（{scene_count} 个场景）")
    print()
    print("--- 口播稿预览 ---")
    print(script[:200] + ("..." if len(script) > 200 else ""))
    print()
    print("--- 分镜预览 ---")
    print(storyboard)


if __name__ == "__main__":
    main()
