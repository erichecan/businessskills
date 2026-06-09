#!/usr/bin/env python3
"""Generate VO audio for 90s composition — zh-CN-XiaoxiaoNeural +20%."""
import asyncio
import subprocess
import json
import os

import edge_tts

VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+20%"

TEXTS = [
    # S1 HOOK 0-8s
    "上次开会，你鼓起勇气发言，说完之后，全场沉默。领导低头看手机，同事们继续刷文件——就好像你没说过一样。",
    # S2 痛点放大 8-17s
    "这种感觉，很多职场人都有。你不是不努力，你认真准备了，但就是说完没人接，提了建议没人理，方案被轻描淡写地否掉了。",
    # S3 观点翻转 17-26s
    "我做职场沟通培训七年，见过太多这样的情况。问题不是你不会说话，是说话的顺序错了。",
    # S4 拆解机制 26-36s
    "大多数人汇报是这样的：先交代背景，再讲过程，最后才说结论。但听的人在等你说重点的时候，早就失去耐心了。",
    # S5 案例一 36-47s
    "我有个学员，财务经理，之前每次申请预算都要来回三四轮。后来她改了一句话的顺序，开口就说：我需要二十万，理由是这三点。五分钟之内，批复下来了。",
    # S6 方法落地 47-57s
    "这就是结论先行。三句话公式：我的结论是什么，支撑这个结论的理由，以及我需要你做什么决定。顺序对了，别人才有可能真的听进去。",
    # S7 案例二对比 57-68s
    "同样一件事，两种说法。第一种：最近用户反馈比较多，我觉得可能是产品体验的问题，所以我们是不是应该考虑做一次优化？第二种：建议下周启动一次产品优化，我整理了三个核心用户痛点，预计两周能完成。你感受一下，哪个更像一个能推动事情的人说的话？",
    # S8 更深一层 68-76s
    "说话的方式，决定了别人脑子里对你的判断。不是「这个人很努力」，而是「这个人说话有逻辑，我知道该怎么配合他」。",
    # S9 社会证明 76-84s
    "七年时间，我训练过五百多个职场人，从基层员工到总监级别。改变他们的，不是口才，是一套清晰的表达结构。",
    # S10 行动指令 84-90s
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
