#!/usr/bin/env python3
"""
make_voice.py — OpenAI TTS 配音生成脚本

用法:
  python make_voice.py --text "做了父亲之后，我才真正看懂了我爸" --output voice.mp3

  # 或从文件读取脚本
  python make_voice.py --file script.txt --output voice.mp3

环境变量:
  OPENAI_API_KEY  — OpenAI API Key

参数说明:
  --voice   声音，默认 onyx（低沉男声，适合情感叙述）
            可选: alloy / echo / fable / onyx / nova / shimmer
  --speed   语速，默认 0.9（略慢，更有分量感）
  --model   默认 tts-1-hd（高质量）
"""

import argparse
import os
import sys
from pathlib import Path


def generate_voice(text: str, output: str, voice: str, speed: float, model: str):
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ 缺少 openai 库，请运行: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ 未设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print(f"▶ 生成配音: {len(text)} 字，声音={voice}，语速={speed}")

    response = client.audio.speech.create(
        model=model,
        voice=voice,
        input=text,
        speed=speed,
    )

    response.stream_to_file(output)
    size = Path(output).stat().st_size / 1024
    print(f"✅ 配音已保存: {output}（{size:.0f} KB）")


def main():
    parser = argparse.ArgumentParser(description="OpenAI TTS 配音生成")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="直接输入文本")
    group.add_argument("--file", help="从文本文件读取脚本")
    parser.add_argument("--output", default="voice.mp3", help="输出 MP3 路径")
    parser.add_argument("--voice", default="onyx", help="声音类型（默认 onyx）")
    parser.add_argument("--speed", type=float, default=0.9, help="语速（默认 0.9）")
    parser.add_argument("--model", default="tts-1-hd", help="TTS 模型（默认 tts-1-hd）")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    else:
        text = args.text.strip()

    if not text:
        print("❌ 文本为空")
        sys.exit(1)

    generate_voice(text, args.output, args.voice, args.speed, args.model)


if __name__ == "__main__":
    main()
