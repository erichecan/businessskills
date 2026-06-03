#!/usr/bin/env python3
"""
make_images.py — 批量生成视频场景图

用法:
  python make_images.py --scenes scenes.txt --output-dir day1/
  python make_images.py --scenes scenes.txt --output-dir day1/ --ref test_images/test_chibi_03.png

scenes.txt 格式（每行一个场景描述，空行跳过）:
  深夜一个人坐在沙发上刷手机，表情疲惫
  清晨站在窗边发呆，窗外天刚亮
  ...

输出:
  day1/scene1.png
  day1/scene2.png
  ...
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# 角色固定 prompt 前缀（只固定人物形象，场景由调用方决定）
CHARACTER_PREFIX = (
    "Generate an image with gpt-image-1. "
    "Keep the SAME character from the reference image: "
    "weathered middle-aged Chinese man in his early 40s, "
    "slim face with rounded jaw (NOT pointed chin), "
    "crow's feet around tired eyes, nasolabial lines, dark circles, "
    "5 o'clock stubble, black hair with gray temples, "
    "heavy-lidded tired eyes conveying 沧桑 and quiet exhaustion. "
    "2D flat anime/webtoon art style, NOT photorealistic. "
    "Vertical 9:16 composition. No text, no watermarks, no subtitles. "
    "Scene: "
)


def generate_image(scene: str, output_path: Path, ref_image: Path, dry_run: bool) -> bool:
    prompt = f"{CHARACTER_PREFIX}{scene}. Save to {output_path.resolve()}"

    cmd = [
        "codex", "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-i", str(ref_image.resolve()),
        "--", prompt,
    ]

    if dry_run:
        print(f"  [DRY RUN] codex exec -i {ref_image.name} -- [prompt]")
        return True

    result = subprocess.run(cmd)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="批量生成视频场景图（调用 Codex CLI + gpt-image-1）")
    parser.add_argument("--scenes", required=True, help="场景描述文件（每行一个场景）")
    parser.add_argument("--output-dir", required=True, help="图片输出目录")
    parser.add_argument(
        "--ref",
        default=None,
        help="角色参考图路径（默认 test_images/test_chibi_03.png）",
    )
    parser.add_argument("--prefix", default="scene", help="输出文件名前缀（默认 scene）")
    parser.add_argument("--start", type=int, default=1, help="起始编号（默认 1，用于断点续跑）")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不实际生成")
    args = parser.parse_args()

    # 路径解析
    scripts_dir = Path(__file__).parent
    project_dir = scripts_dir.parent

    scenes_file = Path(args.scenes)
    if not scenes_file.exists():
        print(f"❌ 找不到场景文件: {args.scenes}")
        sys.exit(1)

    ref_image = Path(args.ref) if args.ref else project_dir / "test_images" / "test_chibi_03.png"
    if not ref_image.exists():
        print(f"❌ 找不到角色参考图: {ref_image}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 读取并过滤场景
    raw = scenes_file.read_text(encoding="utf-8").splitlines()
    scenes = [line.strip() for line in raw if line.strip()]

    if not scenes:
        print("❌ 场景文件为空")
        sys.exit(1)

    print(f"📋 共 {len(scenes)} 个场景")
    print(f"🖼  角色参考: {ref_image}")
    print(f"📁 输出目录: {output_dir}")
    if args.start > 1:
        print(f"⏭  从第 {args.start} 张开始（跳过前 {args.start - 1} 张）")
    print()

    failed = []
    for i, scene in enumerate(scenes, 1):
        if i < args.start:
            continue

        output_path = output_dir / f"{args.prefix}{i}.png"
        short = scene[:60] + ("..." if len(scene) > 60 else "")
        print(f"🎨 [{i}/{len(scenes)}] {short}")

        ok = generate_image(scene, output_path, ref_image, args.dry_run)

        if ok and (args.dry_run or output_path.exists()):
            size_kb = output_path.stat().st_size / 1024 if output_path.exists() else 0
            print(f"  ✅ → {output_path}（{size_kb:.0f} KB）")
        else:
            print(f"  ❌ 生成失败")
            failed.append(i)

        if i < len(scenes) and not args.dry_run:
            time.sleep(2)

    print()
    if failed:
        print(f"⚠️  {len(failed)} 张失败（编号: {failed}）")
        print(f"   重跑失败项: python make_images.py --scenes {args.scenes} --output-dir {args.output_dir} --start {failed[0]}")
    else:
        print(f"✅ 全部完成，{len(scenes)} 张已保存到 {output_dir}")


if __name__ == "__main__":
    main()
