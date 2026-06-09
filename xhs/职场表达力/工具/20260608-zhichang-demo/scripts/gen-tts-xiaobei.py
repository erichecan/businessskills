#!/usr/bin/env python3
"""Generate VO audio with zh-CN-liaoning-XiaobeiNeural (northeastern dialect)."""
import asyncio
import subprocess
import json
import os

import edge_tts

VOICE = "zh-CN-liaoning-XiaobeiNeural"
RATE = "+20%"

TEXTS = [
    "汇报完全没用？领导根本听不进去！",
    "说了半天，对方还是没听进去",
    "提了方案，开口两句就被否了",
    "开口就怂，说完之后又后悔",
    "学会职场表达力，开口就让人记住你",
]

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")

async def gen_one(text: str, idx: int):
    mp3 = os.path.join(ASSETS, f"vo{idx}_tmp.mp3")
    wav = os.path.join(ASSETS, f"vo{idx}.wav")
    c = edge_tts.Communicate(text, VOICE, rate=RATE)
    await c.save(mp3)
    subprocess.run(["ffmpeg", "-y", "-i", mp3, wav], check=True, capture_output=True)
    os.remove(mp3)
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", wav],
        capture_output=True, text=True, check=True,
    )
    duration = float(json.loads(r.stdout)["streams"][0]["duration"])
    return round(duration, 3)

async def main():
    durations = []
    for i, text in enumerate(TEXTS, 1):
        print(f"[vo{i}] {text}")
        d = await gen_one(text, i)
        durations.append(d)
        print(f"       → {d:.3f}s")
    print("\n=== DURATIONS ===")
    for i, d in enumerate(durations, 1):
        print(f"  vo{i}: {d:.3f}s")

asyncio.run(main())
