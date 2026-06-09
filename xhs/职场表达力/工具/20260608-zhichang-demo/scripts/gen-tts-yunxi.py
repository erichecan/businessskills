#!/usr/bin/env python3
"""Generate VO audio — zh-CN-YunxiNeural +20% — full 10-scene rebuild."""
import asyncio
import subprocess
import json
import os

import edge_tts

VOICE = "zh-CN-YunxiNeural"
RATE = "+20%"

TEXTS = [
    # S1 HOOK 0~8s  — 开门见山：这种人存在
    "职场里有一种人，话不多，但开口，所有人都会停下来。不是因为职位，不是因为嗓门——我花了七年才搞清楚，他们做对了什么。",
    # S2 痛点共鸣 ~9s — 你也遇到过
    "你有没有遇到过：同样一个建议，你说出来没人接，另一个同事说出来全场点头——内容几乎一样，结果完全不同。问题出在哪？",
    # S3 核心观点 ~5s — 一句话揭底
    "说话被忽略，原因只有一个：你把结论放在了最后。这不是口才的问题，是顺序的问题。",
    # S4 机制拆解 ~9s — 大脑三秒判断
    "人的大脑在听一句话的前三秒，会先判断：这跟我有关系吗？如果你还在铺背景、说过程，它的注意力已经开始漂移了。等你说到重点，门早就关上了。",
    # S5 案例对比 ~11s — 学员真实改变
    "我有个学员，每次汇报开口就说「最近项目遇到了一些挑战」——老板通常在这里就打断他了。后来他改成：「老板，我需要你帮我做一个决定，两个方案各有利弊，我整理好了。」老板立刻放下了手机。",
    # S6 方法提炼 ~9s — 结论先行是思维
    "同样的内容，不同的顺序。结论先行不是说话技巧，是一种思维方式——开口之前，先替对方想好：他需要从你这里得到什么？",
    # S7 案例二对比 ~20s — 两种说法并排（保持原版）
    "同样一件事，两种说法。第一种：最近用户反馈比较多，我觉得可能是产品体验的问题，所以我们是不是应该考虑做一次优化？第二种：建议下周启动一次产品优化，我整理了三个核心用户痛点，预计两周能完成。你感受一下，哪个更像一个能推动事情的人说的话？",
    # S8 更深一层 ~8s — 判断的是什么
    "说话的方式，决定了别人脑子里对你的判断。不是「这个人很努力」，而是「这个人说话有逻辑，我知道该怎么配合他」。",
    # S9 社会证明 ~9s
    "七年时间，我训练过五百多个职场人，从基层员工到总监级别。改变他们的，不是口才，是一套清晰的表达结构。",
    # S10 行动指令 ~6s
    "如果你也想说话更有分量，关注我，评论区扣一个「1」，我发给你完整的表达框架。",
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
        print(f"[vo{i}] {text[:30]}...")
        d = await gen_one(text, i)
        durations.append(d)
        print(f"       → {d:.3f}s")
    print("\n=== DURATIONS ===")
    for i, d in enumerate(durations, 1):
        print(f"  vo{i}: {d:.3f}s")
    print(f"\n  TOTAL: {sum(durations):.3f}s")


asyncio.run(main())
