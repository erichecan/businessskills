#!/usr/bin/env bash
# produce.sh — 视频号情感视频一键生产
#
# 用法：
#   ./produce.sh <工作目录>
#
# 工作目录结构（提前准备好）：
#   <工作目录>/
#     script.txt      — 配音文案（纯文本）
#     scene1.png      — 场景图片 1
#     scene2.png      — 场景图片 2
#     scene3.png      — 场景图片 3（可有更多）
#     bgm.mp3         — 背景音乐
#
# 输出：
#   <工作目录>/voice.mp3   — 生成的配音
#   <工作目录>/output.mp4  — 最终视频

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${1:-.}"

echo "📁 工作目录: $WORK_DIR"

# 检查必要文件
[[ -f "$WORK_DIR/script.txt" ]] || { echo "❌ 缺少 script.txt"; exit 1; }
[[ -f "$WORK_DIR/bgm.mp3" ]]   || { echo "❌ 缺少 bgm.mp3"; exit 1; }

# 收集图片（按文件名排序）
IMAGES=("$WORK_DIR"/scene*.png)
[[ ${#IMAGES[@]} -gt 0 ]] || { echo "❌ 没有找到 scene*.png 图片"; exit 1; }
echo "🖼  图片: ${#IMAGES[@]} 张 → ${IMAGES[*]}"

# Step 1: 生成配音
echo ""
echo "🎙  Step 1: 生成配音..."
python3 "$SCRIPT_DIR/make_voice.py" \
  --file "$WORK_DIR/script.txt" \
  --output "$WORK_DIR/voice.mp3"

# Step 2: 合成视频
echo ""
echo "🎬 Step 2: 合成视频..."
python3 "$SCRIPT_DIR/produce_video.py" \
  --images "${IMAGES[@]}" \
  --voice  "$WORK_DIR/voice.mp3" \
  --bgm    "$WORK_DIR/bgm.mp3" \
  --output "$WORK_DIR/output.mp4"

echo ""
echo "✅ 完成！视频已生成: $WORK_DIR/output.mp4"
