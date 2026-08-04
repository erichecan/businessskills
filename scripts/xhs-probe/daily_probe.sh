#!/bin/bash
# 探词全自动链路：探测 → 分析 → 回填定级。由 com.eric.xhsprobe 每 6 小时触发。
#
# 三层原本是分开跑的，中间那层要在对话里手动跑 skill，所以「已验证」全靠人工标。
# auto_analyze.py 把第 2 层 headless 化之后，这条链就能一口气跑完：
#   probe.py（抓搜索结果，算竞争密度）
#     → auto_analyze.py（判答案空缺，出 disposition）
#     → backfill.py（做→已验证，缓→候选，放弃→放弃）
# 跑完词库里就多出若干「已验证」的词，refine_loop 直接能用。
#
# limit 10：一轮 10 个词，每词间隔 45-90 秒防风控，约 15-25 分钟。
# 4 次/天 = 40 词/天。当前探测队列 267 个，约一周探完。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DAY=$(date +%Y%m%d)
PY=/usr/bin/python3

echo "===== $(date '+%F %T') 探词链路开始 ====="

"$PY" "$DIR/probe.py" --from-cikuku --limit 10
probe_rc=$?
if [ $probe_rc -ne 0 ]; then
  # 探测失败多半是 CDP 代理没起或小红书要验证码。分析和回填没有新数据可吃，
  # 但仍然跑一遍——之前几轮可能有没分析完的存量。
  echo "⚠️ probe.py 退出码 $probe_rc，继续处理存量"
fi

"$PY" "$DIR/auto_analyze.py" --date "$DAY" --limit 10
"$PY" "$DIR/backfill.py" --date "$DAY"

echo "===== $(date '+%F %T') 探词链路结束 ====="
