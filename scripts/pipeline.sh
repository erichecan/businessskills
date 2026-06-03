#!/usr/bin/env bash
# pipeline.sh — 视频号情感视频完整生产流水线
#
# 用法：
#   ./scripts/pipeline.sh <episode目录>
#
# 目录结构（只需提前准备）：
#   <episode目录>/
#     topic.txt   — 话题描述（1-3句话）
#     bgm.mp3     — 背景音乐
#
# 自动生成：
#   script.txt / storyboard.txt / scene*.png / voice.mp3 / output.mp4

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EP_DIR="${1:-.}"
EP_DIR="$(cd "$EP_DIR" && pwd)"

echo "================================================"
echo "🎬 视频号内容生产流水线"
echo "📁 Episode 目录: $EP_DIR"
echo "================================================"
echo ""

# 检查必要文件
[[ -f "$EP_DIR/topic.txt" ]] || { echo "❌ 缺少 topic.txt"; exit 1; }
[[ -f "$EP_DIR/bgm.mp3" ]]   || { echo "❌ 缺少 bgm.mp3"; exit 1; }

echo "📌 话题: $(cat "$EP_DIR/topic.txt")"
echo ""

# Step 1: 生成口播稿 + 分镜
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📝 Step 1/4: 生成口播稿 + 分镜..."
python3 "$SCRIPT_DIR/make_script.py" \
  --topic-file "$EP_DIR/topic.txt" \
  --output-dir "$EP_DIR"

# Step 2: 生成场景图
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎨 Step 2/4: 生成场景图..."
python3 "$SCRIPT_DIR/make_images.py" \
  --scenes "$EP_DIR/storyboard.txt" \
  --output-dir "$EP_DIR"

# Step 3: 生成配音
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎙  Step 3/4: 生成配音..."
python3 "$SCRIPT_DIR/make_voice.py" \
  --file "$EP_DIR/script.txt" \
  --output "$EP_DIR/voice.mp3"

# Step 4: 合成视频
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎬 Step 4/4: 合成视频..."
IMAGES=("$EP_DIR"/scene*.png)
python3 "$SCRIPT_DIR/produce_video.py" \
  --images "${IMAGES[@]}" \
  --voice  "$EP_DIR/voice.mp3" \
  --bgm    "$EP_DIR/bgm.mp3" \
  --output "$EP_DIR/output.mp4"

echo ""
echo "================================================"
echo "✅ 完成！"
echo "   视频: $EP_DIR/output.mp4"
echo "================================================"
