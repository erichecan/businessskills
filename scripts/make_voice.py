#!/usr/bin/env python3
"""
make_voice.py — Edge TTS 配音生成脚本（免费，无需 API Key）

用法:
  python make_voice.py --file script.txt --output voice.mp3
  python make_voice.py --text "做了父亲之后，我才真正看懂了我爸" --output voice.mp3

参数说明:
  --voice   声音，默认 zh-CN-YunjianNeural（激情男声，适合情感叙述）
            可选男声: YunjianNeural / YunyangNeural / YunxiNeural / YunxiaNeural
  --rate    语速偏移，默认 -10%（略慢，更有分量感）
            正值加速（+10%），负值减速（-20%）
  --volume  音量偏移，默认 +0%
"""

import argparse
import asyncio
import sys
from pathlib import Path

import edge_tts


async def _generate(text: str, output: str, voice: str, rate: str, volume: str):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output)


def generate_voice(text: str, output: str, voice: str, rate: str, volume: str):
    print(f"▶ 生成配音: {len(text)} 字，声音={voice}，语速={rate}")
    asyncio.run(_generate(text, output, voice, rate, volume))
    size = Path(output).stat().st_size / 1024
    print(f"✅ 配音已保存: {output}（{size:.0f} KB）")


def main():
    parser = argparse.ArgumentParser(description="Edge TTS 配音生成（免费）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="直接输入文本")
    group.add_argument("--file", help="从文本文件读取脚本")
    parser.add_argument("--output", default="voice.mp3", help="输出 MP3 路径")
    parser.add_argument("--voice", default="zh-CN-YunjianNeural", help="声音（默认 YunjianNeural）")
    parser.add_argument("--rate", default="-10%", help="语速偏移（默认 -10%%）")
    parser.add_argument("--volume", default="+0%", help="音量偏移（默认 +0%%）")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"❌ 找不到文件: {args.file}")
            sys.exit(1)
        text = path.read_text(encoding="utf-8").strip()
    else:
        text = args.text.strip()

    if not text:
        print("❌ 文本为空")
        sys.exit(1)

    generate_voice(text, args.output, args.voice, args.rate, args.volume)


if __name__ == "__main__":
    main()
