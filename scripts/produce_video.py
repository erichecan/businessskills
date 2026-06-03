#!/usr/bin/env python3
"""
produce_video.py — 视频号情感视频合成脚本

流程：图片列表 + 配音MP3 + BGM → 9:16 竖屏 MP4

用法:
  python produce_video.py \
    --images scene1.png scene2.png scene3.png scene4.png \
    --voice  voiceover.mp3 \
    --bgm    bgm.mp3 \
    --output output.mp4

可选参数:
  --transition  淡入淡出时长，秒，默认 1.0
  --bgm-vol     BGM 音量比例，默认 0.18（配音的18%）
  --fps         帧率，默认 30
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def get_audio_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    raise RuntimeError(f"无法读取音频时长: {path}")


def build_command(
    images: list[str],
    voice: str,
    bgm: str,
    output: str,
    transition: float,
    bgm_vol: float,
    fps: int,
) -> list[str]:
    voice_duration = get_audio_duration(voice)
    n = len(images)
    # 每张图的净显示时长（overlap 由 xfade 处理）
    per_image = voice_duration / n
    # 每个输入流的实际时长要比净时长多一个 transition，否则最后一帧会闪黑
    input_duration = per_image + transition

    cmd = ["ffmpeg", "-y"]

    # 图片输入
    for img in images:
        cmd += ["-loop", "1", "-t", str(input_duration), "-i", img]

    # 音频输入
    voice_idx = n
    bgm_idx = n + 1
    cmd += ["-i", voice, "-i", bgm]

    # filter_complex
    parts = []

    # 每张图缩放到 1080×1920，保持比例并居中裁剪
    for i in range(n):
        parts.append(
            f"[{i}:v]"
            f"scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"fps={fps},"
            f"setpts=PTS-STARTPTS"
            f"[v{i}]"
        )

    # xfade 串联
    if n == 1:
        parts.append("[v0]copy[video]")
    else:
        prev = "v0"
        for i in range(1, n):
            offset = per_image * i - transition * (i - 1) - transition
            offset = max(0.05, offset)
            label = "video" if i == n - 1 else f"xf{i}"
            parts.append(
                f"[{prev}][v{i}]"
                f"xfade=transition=fade:duration={transition}:offset={offset:.3f}"
                f"[{label}]"
            )
            prev = label

    # 音频混合
    parts.append(f"[{voice_idx}:a]volume=1.0[voice]")
    parts.append(f"[{bgm_idx}:a]volume={bgm_vol}[bgm]")
    parts.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[audio]")

    filter_complex = ";\n".join(parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[video]",
        "-map", "[audio]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-r", str(fps),
        "-shortest",
        output,
    ]

    return cmd, voice_duration, per_image


def main():
    parser = argparse.ArgumentParser(description="视频号情感视频合成")
    parser.add_argument("--images", nargs="+", required=True, help="场景图片路径列表（按顺序）")
    parser.add_argument("--voice", required=True, help="配音 MP3 路径")
    parser.add_argument("--bgm", required=True, help="背景音乐 MP3 路径")
    parser.add_argument("--output", default="output.mp4", help="输出视频路径")
    parser.add_argument("--transition", type=float, default=1.0, help="场景淡入淡出时长（秒）")
    parser.add_argument("--bgm-vol", type=float, default=0.18, help="BGM 相对配音的音量比例")
    parser.add_argument("--fps", type=int, default=30, help="帧率")
    args = parser.parse_args()

    # 检查文件
    missing = [f for f in args.images + [args.voice, args.bgm] if not Path(f).exists()]
    if missing:
        print(f"❌ 找不到文件: {', '.join(missing)}")
        sys.exit(1)

    print(f"▶ 图片: {len(args.images)} 张")

    try:
        cmd, total, per = build_command(
            args.images, args.voice, args.bgm, args.output,
            args.transition, args.bgm_vol, args.fps,
        )
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"▶ 配音时长: {total:.1f}s，每张图 {per:.1f}s，转场 {args.transition}s")
    print(f"▶ 输出: {args.output}")
    print()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("❌ FFmpeg 合成失败，请检查上方错误信息")
        sys.exit(1)

    size = Path(args.output).stat().st_size / 1024 / 1024
    print(f"\n✅ 合成完成: {args.output}（{size:.1f} MB）")


if __name__ == "__main__":
    main()
