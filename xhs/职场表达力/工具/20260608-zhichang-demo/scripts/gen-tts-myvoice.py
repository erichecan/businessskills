#!/usr/bin/env python3
"""Generate VO audio for all 10 scenes using cloned voice via Fish Audio API.
Speed: +20% via Prosody. Post-processing: warmth EQ + compression + subtle reverb.
"""
import json
import os
import subprocess
import sys

try:
    from fish_audio_sdk import Session, TTSRequest
    from fish_audio_sdk.schemas import Prosody
except ImportError:
    print("Installing fish-audio-sdk...")
    subprocess.run([sys.executable, "-m", "pip", "install", "fish-audio-sdk"], check=True)
    from fish_audio_sdk import Session, TTSRequest
    from fish_audio_sdk.schemas import Prosody

API_KEY  = "64d142eeaec9456cacb08f28c2055cfb"
VOICE_ID = "18a3192f9a684c16b60cf3880e6d0cce"

TEXTS = [
    # S1 HOOK 0~10s
    "职场里有一种人，话不多，但开口，所有人都会停下来。不是因为职位，不是因为嗓门——我花了七年才搞清楚，他们做对了什么。",
    # S2 痛点共鸣 ~10s
    "你有没有遇到过：同样一个建议，你说出来没人接，另一个同事说出来全场点头——内容几乎一样，结果完全不同。问题出在哪？",
    # S3 核心观点 ~8s
    "说话被忽略，原因只有一个：你把结论放在了最后。这不是口才的问题，是顺序的问题。",
    # S4 机制拆解 ~12s
    "人的大脑在听一句话的前三秒，会先判断：这跟我有关系吗？如果你还在铺背景、说过程，它的注意力已经开始漂移了。等你说到重点，门早就关上了。",
    # S5 案例对比 ~13s
    "我有个学员，每次汇报开口就说「最近项目遇到了一些挑战」——老板通常在这里就打断他了。后来他改成：「老板，我需要你帮我做一个决定，两个方案各有利弊，我整理好了。」老板立刻放下了手机。",
    # S6 方法提炼 ~10s
    "同样的内容，不同的顺序。结论先行不是说话技巧，是一种思维方式——开口之前，先替对方想好：他需要从你这里得到什么？",
    # S7 案例二对比 ~20s
    "同样一件事，两种说法。第一种：最近用户反馈比较多，我觉得可能是产品体验的问题，所以我们是不是应该考虑做一次优化？第二种：建议下周启动一次产品优化，我整理了三个核心用户痛点，预计两周能完成。你感受一下，哪个更像一个能推动事情的人说的话？",
    # S8 更深一层 ~9s
    "说话的方式，决定了别人脑子里对你的判断。不是「这个人很努力」，而是「这个人说话有逻辑，我知道该怎么配合他」。",
    # S9 社会证明 ~10s
    "七年时间，我训练过五百多个职场人，从基层员工到总监级别。改变他们的，不是口才，是一套清晰的表达结构。",
    # S10 行动指令 ~6s
    "如果你也想说话更有分量，关注我，评论区扣一个「1」，我发给你完整的表达框架。",
]

# ffmpeg filter chain for magnetic voice (same as gen-tts-myvoice-s1.py)
MAGNETIC_FILTER = (
    "equalizer=f=120:width_type=o:width=2:g=4,"
    "equalizer=f=3500:width_type=o:width=2:g=2,"
    "equalizer=f=7500:width_type=o:width=2:g=-3,"
    "acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2,"
    "aecho=0.8:0.7:30:0.12"
)

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")


def get_duration(wav_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", wav_path],
        capture_output=True, text=True, check=True,
    )
    return round(float(json.loads(r.stdout)["streams"][0]["duration"]), 3)


def gen_one(session: Session, text: str, idx: int) -> float:
    mp3     = os.path.join(ASSETS, f"vo{idx}_tmp.mp3")
    wav_raw = os.path.join(ASSETS, f"vo{idx}_raw.wav")
    wav     = os.path.join(ASSETS, f"vo{idx}.wav")

    with open(mp3, "wb") as f:
        for chunk in session.tts(TTSRequest(
            reference_id=VOICE_ID,
            text=text,
            prosody=Prosody(speed=1.2),
        )):
            f.write(chunk)

    subprocess.run(["ffmpeg", "-y", "-i", mp3, wav_raw], check=True, capture_output=True)
    os.remove(mp3)

    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_raw, "-af", MAGNETIC_FILTER, wav],
        check=True, capture_output=True,
    )
    os.remove(wav_raw)

    return get_duration(wav)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    durations = []
    with Session(API_KEY) as session:
        for i, text in enumerate(TEXTS, 1):
            print(f"[vo{i}] {text[:30]}...")
            d = gen_one(session, text, i)
            durations.append(d)
            print(f"       → {d:.3f}s")

    print("\n=== DURATIONS ===")
    for i, d in enumerate(durations, 1):
        print(f"  vo{i}: {d:.3f}s")
    print(f"\n  TOTAL: {sum(durations):.3f}s")
    print("\n把上面的时长复制给 Claude，更新 index.html 的时间轴。")


if __name__ == "__main__":
    main()
