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
# ⚠️ probe.py 有硬上限 MAX_KEYWORDS_PER_RUN=5（反封控），传 --limit 10 会被截断回 5。
# 所以这里就写 5，别写一个会被静默截断的数字骗自己。
# 实际吞吐：5 词/轮 × 4 轮/天 = 20 词/天，每轮约 8-12 分钟（词间隔 45-90 秒）。
# 要提速得改 probe.py 的 MAX_KEYWORDS_PER_RUN —— 那是反封控参数，账号被封损失远大于
# 早几天探完，不单方面调。
#
# QUEUE_FLOOR：探测队列低于这个数就从关键词池自动补货。不补的话，队列探完就空了，
# 关键词池里剩下的几百个词永远不会被探测（import_pool.py 原本是手动跑的）。
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DAY=$(date +%Y%m%d)
PY="${XHS_PY:-/opt/homebrew/bin/python3}"   # runner 会注入 XHS_PY；手动跑时退到 Homebrew 的 3.14
# ⛔ 别改回 /usr/bin/python3：那是 3.9.6，跑不了仓库里的 3.10+ 语法（见 launchd_runner.py 顶部）
QUEUE_FLOOR=40
REFILL=100

echo "===== $(date '+%F %T') 探词链路开始 ====="

queue=$("$PY" -c "
import sys; sys.path.insert(0,'$DIR')
import probe; print(len(probe.load_pending(99999)))
" 2>/dev/null || echo 0)
echo "探测队列剩余 $queue 个词"
if [ "$queue" -lt "$QUEUE_FLOOR" ]; then
  echo "低于下限 ${QUEUE_FLOOR}，从关键词池补 $REFILL 个"
  "$PY" "$DIR/import_pool.py" --limit "$REFILL"
fi

# 2026-08-12 从 probe.py（CDP）换到 probe_opencli.py。
# 原因：CDP Proxy 连的是采集专用 profile（9333, ~/.xhs-chrome-profile），那个 profile 的
# www.xiaohongshu.com **只有游客 cookie**（无 web_session），搜索页永远是登录墙。
# 当天 18:30 和 22:01 两轮分别报「判定被限流」和「触发安全验证」，全是这个登录墙的误报。
# opencli 走 Browser Bridge 附着日常 Chrome，用的就是平时刷网页那份 cookie。
# 要退回旧路径：把下面这行的 probe_opencli.py 改回 probe.py（但先确认 9333 profile 已登录）。
"$PY" "$DIR/probe_opencli.py" --from-cikuku --limit 5
probe_rc=$?
if [ $probe_rc -ne 0 ]; then
  # 探测失败多半是 opencli daemon/扩展没连上，或登录态过期（跑 opencli xiaohongshu whoami 验）。
  # 分析和回填没有新数据可吃，但仍然跑一遍——之前几轮可能有没分析完的存量。
  echo "⚠️ probe_opencli.py 退出码 ${probe_rc}，继续处理存量"
fi

"$PY" "$DIR/auto_analyze.py" --date "$DAY" --limit 10
"$PY" "$DIR/backfill.py" --date "$DAY"

echo "===== $(date '+%F %T') 探词链路结束 ====="

# ⛔ 2026-08-13：这里原先什么都不做，脚本的退出码于是等于最后一条命令（backfill）的。
# 结果当天三轮探测全挂（launchd 的 PATH 里没有 opencli，每轮抓 0 条）却一路退 0，
# brief 照报「✅ 采集探测 — 上次退出 0」，整整一天没人知道采不到数据。
# 「探测挂了也要继续处理存量」这个决定本身是对的，但继续跑和如实上报是两回事 ——
# 咽下退出码等于把唯一的报警渠道也一并关掉了。
if [ "$probe_rc" -ne 0 ]; then
  echo "⛔ 本轮探测失败（probe_opencli.py 退出码 ${probe_rc}），存量已照常处理。"
  exit "$probe_rc"
fi
