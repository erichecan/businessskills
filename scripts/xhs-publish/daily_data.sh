#!/bin/bash
# 数据回流链路：抓后台数据 → 预测对账 → 案例库补货。每天 21:15 跑一次。
#
# 补的是三个「写完没人调度」的断点（2026-08-03 查出来）：
#   fetch_stats.py        抓创作后台数据回填 —— 一直没有 plist，纯手动
#   review_prediction.py  预测对账 —— 有数据也没人对账，模型系数永远停在先验值
#   harvest_cases.py      评论区原话 → 案例库 —— 采集一直在攒原话，但没人往案例库搬
#
# 为什么放一起、放在晚上：三件事都依赖「今天的数据已经产生」。
# fetch_stats 要当天的观看/互动数，harvest_cases 要当天采集入库的原话
# （采集任务 0/6/12/18 点跑，21:15 时当天四轮都跑完了）。
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${XHS_PY:-/opt/homebrew/bin/python3}"   # runner 会注入 XHS_PY；手动跑时退到 Homebrew 的 3.14
# ⛔ 别改回 /usr/bin/python3：那是 3.9.6，跑不了仓库里的 3.10+ 语法（见 launchd_runner.py 顶部）

echo "===== $(date '+%F %T') 数据回流开始 ====="

# ⛔ 2026-08-13：三步原先都是 `|| echo 继续`，脚本退出码等于最后一条命令的。
# 「一步失败不拖累另外两步」这个决定是对的（三件事互不依赖），
# 但把失败咽下去是另一回事 —— brief 只看得到退出码，全咽了它就永远报 ✅。
# 同一个病 daily_probe.sh 也犯过：2026-08-13 探测三轮全挂却报「✅ 退出 0」，
# 挂了一整天没人知道。改成：照样各跑各的，但记下谁挂了，最后如实上报。
failed=""

# 抓不到数据不是致命错误（CDP 代理没起、后台改版都会失败），后面两步不依赖它，继续跑。
"$PY" "$REPO/scripts/xhs-publish/fetch_stats.py" || { failed="$failed fetch_stats"; echo "⚠️ fetch_stats 失败，继续"; }

# 只对账发布满 7 天的，没有够龄的就直接跳过，不会空转
"$PY" "$REPO/scripts/xhs-loop/review_prediction.py" || { failed="$failed review_prediction"; echo "⚠️ review_prediction 失败，继续"; }

# 每天最多补 20 条，够成稿用就行——一次灌太多会把待确认的半成品案例堆成山
"$PY" "$REPO/scripts/case-entry/harvest_cases.py" --limit 20 || { failed="$failed harvest_cases"; echo "⚠️ harvest_cases 失败"; }

echo "===== $(date '+%F %T') 数据回流结束 ====="

if [ -n "$failed" ]; then
  echo "⛔ 本轮有步骤失败：${failed# }（其余步骤已照常跑完）"
  exit 1
fi
