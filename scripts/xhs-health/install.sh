#!/bin/bash
# 安装素材库健康检查 LaunchAgent（用户级，无需 sudo）
# 用法：bash scripts/xhs-health/install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents/com.eric.xhshealth.plist"
cp "$DIR/com.eric.xhshealth.plist" "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "✅ 已安装：每天 09:30 / 19:30 自动检查，日志在 ~/Library/Logs/xhs/"
echo "   手动检查：python3 $DIR/health_check.py"
echo "   卸载：launchctl unload $DEST && rm $DEST"
