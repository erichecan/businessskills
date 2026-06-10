#!/usr/bin/env python3
"""Generate S1 test clip using cloned voice via Fish Audio API.
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

TEXT = "职场里有一种人，话不多，但开口，所有人都会停下来。不是因为职位，不是因为嗓门——我花了七年才搞清楚，他们做对了什么。"

# ffmpeg filter chain for magnetic voice:
#   eq1: +4dB @ 120Hz  — low warmth / body
#   eq2: +2dB @ 3500Hz — presence / intimacy
#   eq3: -3dB @ 7500Hz — de-ess / remove harshness
#   compressor: even dynamics, confident delivery
#   echo: 30ms subtle room depth
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


def main():
    os.makedirs(ASSETS, exist_ok=True)
    mp3     = os.path.join(ASSETS, "test_vo1_tmp.mp3")
    wav_raw = os.path.join(ASSETS, "test_vo1_raw.wav")
    wav     = os.path.join(ASSETS, "test_vo1.wav")

    print("正在调用 Fish Audio 克隆声音（速度 ×1.2）…")
    with Session(API_KEY) as session:
        with open(mp3, "wb") as f:
            for chunk in session.tts(TTSRequest(
                reference_id=VOICE_ID,
                text=TEXT,
                prosody=Prosody(speed=1.2),
            )):
                f.write(chunk)

    # step 1: mp3 → raw wav
    subprocess.run(["ffmpeg", "-y", "-i", mp3, wav_raw], check=True, capture_output=True)
    os.remove(mp3)

    # step 2: apply magnetic post-processing
    print("正在应用磁性音色处理（EQ + 压缩 + 混响）…")
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_raw, "-af", MAGNETIC_FILTER, wav],
        check=True, capture_output=True,
    )
    os.remove(wav_raw)

    duration = get_duration(wav)
    print(f"✅ 生成完成: assets/test_vo1.wav")
    print(f"⏱ 时长: {duration:.3f}s")
    return duration


if __name__ == "__main__":
    main()
