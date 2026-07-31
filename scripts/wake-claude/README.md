# wake-claude

每天 **00:00 / 06:00 / 12:00 / 18:00 前 1 分钟**(即 **23:59 / 05:59 / 11:59 / 17:59**)
把这台 Mac 从睡眠中唤醒,并确保 **Claude Desktop** 在运行。

## 工作原理

Apple Silicon 睡眠时,只有 `pmset` 安排的**硬件唤醒**能把机器叫醒;`launchd`/`cron` 在睡眠期间不会跑。
但 `pmset repeat` 只支持单个时间点,4 个点用「自续期」方式解决:

| 组件 | 身份 | 职责 |
|------|------|------|
| `com.eric.wakeclaude` (LaunchDaemon) | root | 每天 4 个时间点触发脚本;睡眠时由 pmset 在同一时刻唤醒后补跑 |
| `wake-claude.sh` | root | ①用 `pmset schedule wake` 续排未来 8 个唤醒点 ②`open -a Claude`(仅在未运行时) |

每次成功唤醒并跑一次脚本,就会把后面 ~2 天的唤醒点重新排满,形成自维持循环。

## 安装

```bash
sudo bash scripts/wake-claude/install.sh
```

安装后会立即触发一次,排定首批唤醒点。验证:

```bash
pmset -g sched              # 应看到 4~8 条 'wake at ...' 由我们排定
tail -f /var/log/wake-claude.log
```

## 卸载

```bash
sudo bash scripts/wake-claude/uninstall.sh
```

## 重要前提与限制

- ⚠️ **只对睡眠/合盖待机有效**。Apple Silicon **无法定时开机**——到点时若机器彻底关机,任何脚本都叫不醒它。
- 合盖且未接电源时唤醒通常是 **DarkWake**(屏幕不亮),脚本仍会执行,Claude 在后台启动。
- 唤醒点已硬编码为 `05:59 / 11:59 / 17:59 / 23:59`。要改时间:同时修改
  `wake-claude.sh` 的 `WAKE_HHMM` 和 `com.eric.wakeclaude.plist` 的 `StartCalendarInterval`,然后重新安装。
- 改完脚本/plist 后重新 `sudo bash install.sh` 即可热更新。

## 排查

```bash
# 守护进程状态
sudo launchctl print system/com.eric.wakeclaude | grep -E 'state|last exit'
# 手动触发一次
sudo launchctl kickstart -k system/com.eric.wakeclaude
# 看日志
tail -50 /var/log/wake-claude.log
```
