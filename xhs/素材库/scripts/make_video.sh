#!/bin/bash
# ============================================================
# make_video.sh — HyperFrame 生成画面 + FFmpeg 合并人声
# Usage: bash scripts/make_video.sh <voice.mp3> "<标题>" <成稿.md> <output.mp4>
# ============================================================
set -euo pipefail

VOICE_FILE="$1"
TITLE="$2"
SCRIPT_MD="$3"
OUTPUT_MP4="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d /tmp/xhs_video_XXXXXX)"
trap "rm -rf $TMP" EXIT

# 加载配置
[ -f "$SCRIPT_DIR/produce_config.env" ] && source "$SCRIPT_DIR/produce_config.env"
HYPERFRAME_BIN="${HYPERFRAME_BIN:-hyperframe}"

echo ""
echo "══════════════════════════════════════════"
echo "  🎬  视频生产流水线"
echo "  标题：$TITLE"
echo "══════════════════════════════════════════"
echo "  音频：$VOICE_FILE"
echo "  成稿：$SCRIPT_MD"
echo "  输出：$OUTPUT_MP4"
echo ""

# 确保输出目录存在
mkdir -p "$(dirname "$OUTPUT_MP4")"

# ============================================================
# STEP 1: HyperFrame 生成视频画面
# ============================================================
echo "▶  Step 1: HyperFrame 生成视频画面..."
FRAMES_VIDEO="$TMP/frames.mp4"

# ⚠️  TODO: 把下面的占位命令替换成你实际使用的 HyperFrame CLI 命令
# ---------------------------------------------------------------
# 参数说明（按你的实际 CLI 替换）：
#   $SCRIPT_MD  → 成稿 markdown 文件路径
#   $TITLE      → 视频标题
#   $VOICE_FILE → 人声音频（部分版本的 HyperFrame 可直接接收音频做字幕同步）
#   $FRAMES_VIDEO → 输出视频文件（无声/有声均可，下一步 FFmpeg 会处理）
#
# 示例一（如果 HyperFrame 接受 markdown + 参数）：
#   "$HYPERFRAME_BIN" \
#       --input   "$SCRIPT_MD" \
#       --title   "$TITLE" \
#       --bg      "#1a1a1a" \
#       --fg      "#e8d5a3" \
#       --size    "1080x1920" \
#       --output  "$FRAMES_VIDEO"
#
# 示例二（如果 HyperFrame 接受 JSON config）：
#   python "$SCRIPT_DIR/gen_hf_config.py" "$SCRIPT_MD" "$TITLE" > "$TMP/config.json"
#   "$HYPERFRAME_BIN" render "$TMP/config.json" --output "$FRAMES_VIDEO"
#
# ⚠️  填完真实命令后，删除下面的 echo + exit 两行：
echo "⚠️  请把 scripts/make_video.sh STEP 1 里的占位命令替换为真实的 HyperFrame 命令"
exit 1
# ---------------------------------------------------------------

echo "  ✅ HyperFrame 完成 → $FRAMES_VIDEO"

# ============================================================
# STEP 2: FFmpeg 合并画面 + 人声
# ============================================================
echo ""
echo "▶  Step 2: FFmpeg 合并音视频..."

ffmpeg -y \
    -i "$FRAMES_VIDEO" \
    -i "$VOICE_FILE" \
    -c:v copy \
    -c:a aac -b:a 192k \
    -map 0:v:0 \
    -map 1:a:0 \
    -shortest \
    "$OUTPUT_MP4" \
    2>&1 | grep -E "(Error|error|Output|Duration|Stream)" || true

SIZE=$(du -sh "$OUTPUT_MP4" 2>/dev/null | cut -f1 || echo "?")
echo ""
echo "══════════════════════════════════════════"
echo "  ✅ 视频合成完成！"
echo "  文件：$OUTPUT_MP4"
echo "  大小：$SIZE"
echo "══════════════════════════════════════════"
