#!/bin/bash
#
# 安装 wake-claude：把脚本与 LaunchDaemon 拷到系统位置并加载。
# 用法： sudo bash install.sh
#
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "❌ 需要 root 权限。请运行： sudo bash install.sh"
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.eric.wakeclaude"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"

echo "→ 拷贝脚本到 /usr/local/bin/"
/usr/bin/install -d -m 755 /usr/local/bin
/usr/bin/install -m 755 "$SRC/wake-claude.sh" /usr/local/bin/wake-claude.sh

echo "→ 拷贝 LaunchDaemon 到 $PLIST"
/usr/bin/install -m 644 -o root -g wheel "$SRC/${LABEL}.plist" "$PLIST"

echo "→ 准备日志文件 /var/log/wake-claude.log"
/usr/bin/touch /var/log/wake-claude.log

echo "→ 加载 LaunchDaemon"
/bin/launchctl bootout "system/${LABEL}" 2>/dev/null || true
/bin/launchctl bootstrap system "$PLIST"
/bin/launchctl enable "system/${LABEL}"

echo "→ 立即触发一次（排定首批唤醒点 + 检查 Claude）"
/bin/launchctl kickstart -k "system/${LABEL}"
sleep 2

echo
echo "✅ 安装完成。当前 pmset 调度："
/usr/bin/pmset -g sched
echo
echo "日志： tail -f /var/log/wake-claude.log"
