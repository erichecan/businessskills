#!/bin/bash
#
# 卸载 wake-claude：停止并删除 LaunchDaemon、脚本，并清掉它排的唤醒点。
# 用法： sudo bash uninstall.sh
#
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "❌ 需要 root 权限。请运行： sudo bash uninstall.sh"
  exit 1
fi

LABEL="com.eric.wakeclaude"

echo "→ 卸载 LaunchDaemon"
/bin/launchctl bootout "system/${LABEL}" 2>/dev/null || true

echo "→ 删除文件"
rm -f "/Library/LaunchDaemons/${LABEL}.plist"
rm -f /usr/local/bin/wake-claude.sh

echo "→ 清除 pmset 唤醒调度"
/usr/bin/pmset schedule cancelall 2>/dev/null || true

echo "✅ 已卸载。日志文件 /var/log/wake-claude.log 保留（如需可手动删除）。"
