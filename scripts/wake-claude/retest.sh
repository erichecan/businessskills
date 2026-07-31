#!/bin/bash
# 在真机上：重新触发守护脚本(用真机时钟排点) + 追加一个 N 分钟后的测试唤醒。
# 用法(需 root)： bash retest.sh [分钟数，默认5]
MIN="${1:-5}"
LABEL="com.eric.wakeclaude"

echo "真机当前时间: $(date '+%Y-%m-%d %H:%M:%S')"

# 1) 重新跑守护脚本，让它按真机时钟重排 8 个常规唤醒点
/bin/launchctl kickstart -k "system/${LABEL}" 2>/dev/null
sleep 2

# 2) 追加一个真正“N 分钟后”的测试唤醒(在 kickstart 之后追加，避免被 cancelall 清掉)
WT=$(date -v+"${MIN}"M "+%m/%d/%Y %H:%M:%S")
/usr/bin/pmset schedule wake "$WT"
echo "已追加测试唤醒点: $WT"

echo
echo "=== 真机当前完整调度 ==="
/usr/bin/pmset -g sched

echo
echo "=== 守护脚本日志(末尾) ==="
tail -12 /var/log/wake-claude.log
