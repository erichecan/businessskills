#!/usr/bin/env python3
"""所有 launchd 任务的统一入口 —— 存在的唯一理由是把 TCC 授权收敛成一条。

背景（2026-08-05）：本仓库在 USB 外置卷 /Volumes/datacenter 上，launchd 起的进程
默认没有该卷的读权限（TCC），7 个定时任务全部以退出码 2/126 静默失败，
报错只进 /tmp/*.log，断了多少天没人知道。

修法本来是「去完全磁盘访问权限里把解释器加进去」，但两个坑：
  ① /usr/bin/python3 和 CommandLineTools 里的 python3 都是**符号链接**，
     FDA 的文件选择器不接受符号链接，面板里是灰的点不动；
  ② 就算点得动，那 7 个任务分别用 /usr/bin/python3 和 /bin/bash 两个解释器，
     要授权两次，将来加新任务还可能再多一个。

所以全部改走这个 runner，Program 一律是 /opt/homebrew/bin/python3。
它的进程映像是 .../Python.framework/.../Resources/Python.app/Contents/MacOS/Python
—— 一个 **.app 包**，FDA 面板里永远可选（应用不会被置灰）。授权一次，7 个任务全活。

顺带修掉的第二个 bug：/usr/bin/python3 是 3.9，而 refine_loop.py 用了 3.10+ 的
`dict | None` 注解，加载就抛 TypeError。即使 TCC 修好，写稿任务照样起不来。
Homebrew 的是 3.14，没有这个问题。

用法（plist 里这么写）：
  <string>/opt/homebrew/bin/python3</string>
  <string>.../scripts/launchd_runner.py</string>
  <string>xhs-health/nightly_brief.py</string>      ← 相对 scripts/ 的路径
  <string>--任意参数</string>
.sh 结尾的会用 bash 跑（作为本进程的子进程，因此继承同一份 TCC 授权）。
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent

# ── 日志（2026-08-13 加）────────────────────────────────────────────────────
# 原先 7 个 plist 全都把 stdout/stderr 指到 /tmp/xhs*.log。问题在 2026-08-13 暴露：
# probe 连挂三轮、每轮抓 0 条，回头查日志时 /tmp 下那些文件**一个都不在**
# （macOS 会定期清 /tmp），只能靠翻探测原始 JSON 里的 _error 才找出根因。
# 挪到 ~/Library/Logs/xhs/，按天切分：
#   · 必须在**本机盘**。仓库在外置卷上，卷没挂载时 launchd 连日志文件都打不开，
#     那就退回「失败了但没有任何记录」的老问题 —— 日志得比它记录的东西活得久。
#   · ~/Library/Logs 是 macOS 标准位置，Console.app 直接能看。
LOG_DIR = Path.home() / "Library/Logs/xhs"
LOG_KEEP_DAYS = 30

# ── PATH（2026-08-13 加）────────────────────────────────────────────────────
# launchd 给的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin —— 没有 /opt/homebrew/bin。
# 于是任何用**裸名**调外部 CLI 的脚本，手动跑必通、launchd 跑必挂。
# 2026-08-13 实测代价：probe_opencli.py 调 `opencli` 报
# 「[Errno 2] No such file or directory: 'opencli'」，三轮探测全废，
# 而 daily_probe.sh 把失败吞成 echo、brief 照报「✅ 采集探测 退出 0」，挂了一整天没人知道。
#
# 这跟 2026-08-05 的 TCC 坑是同一类：**launchd 环境 ≠ shell 环境**。
# TCC 那次的解法是把 7 个任务收敛到这一个 runner、只授权一次；
# PATH 也照此办理 —— 在这里补一次，后面任何脚本调任何外部命令都不用再各自操心。
EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin",
              str(Path.home() / ".local/bin"), str(Path.home() / ".bun/bin")]


def child_env():
    """给子进程一份「像人在终端里跑」的环境。只加不减，不覆盖已有值。"""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    cur = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").split(":")
    env["PATH"] = ":".join([p for p in EXTRA_PATH if p not in cur] + cur)
    # 让 .sh 里的 python 调用跟 runner 用同一个解释器。
    # 2026-08-13 发现 daily_data.sh / daily_probe.sh 都硬编码 PY=/usr/bin/python3
    # ——那是 3.9.6，而 runner 是 3.14.6。文件顶部注释早就记过这个坑：
    # refine_loop.py 用了 3.10+ 的 `dict | None` 注解，3.9 加载就抛 TypeError。
    # 两个解释器并存意味着「同一份代码，看谁调它决定能不能跑」，迟早再踩。
    env["XHS_PY"] = sys.executable
    return env


def prune_logs():
    cutoff = time.time() - LOG_KEEP_DAYS * 86400
    for f in LOG_DIR.glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def main():
    if len(sys.argv) < 2:
        print("用法：launchd_runner.py <scripts/ 下的相对路径> [参数...]", file=sys.stderr)
        return 2
    rel, rest = sys.argv[1], sys.argv[2:]

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prune_logs()
    task = rel.replace("/", "-").removesuffix(".py").removesuffix(".sh")
    log = LOG_DIR / f"{task}-{datetime.now():%Y-%m-%d}.log"

    with log.open("a", encoding="utf-8") as lf:
        def say(msg, err=False):
            """两处都写：日志文件是给事后查的，stdout 进 plist 的兜底文件。"""
            print(msg, file=sys.stderr if err else sys.stdout, flush=True)
            lf.write(msg + "\n")
            lf.flush()

        target = SCRIPTS / rel
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not target.exists():
            say(f"[{stamp}] ⛔ 找不到 {target}", err=True)
            return 2

        # 外置卷没挂载时报错要说人话。原先的 "Operation not permitted" 让人以为是权限，
        # 其实盘拔了也是同一类症状，两者得分得开。
        if not REPO.exists():
            say(f"[{stamp}] ⛔ 仓库路径不可达：{REPO}（外置卷未挂载？）", err=True)
            return 2

        cmd = (["/bin/bash", str(target)] if target.suffix == ".sh"
               else [sys.executable, str(target)]) + rest
        say(f"[{stamp}] ▶ {rel} {' '.join(rest)}")
        # cwd 固定在仓库根：好几个脚本用相对路径读 xhs/素材库，
        # launchd 起的进程 cwd 是 / ，不设的话它们会去 /xhs/素材库 找。
        # 子进程的 stdout/stderr 直接写进日志文件 —— 不经 Python 转发，
        # 避免大输出时的缓冲错位，也不会把子进程的实时性拖没。
        r = subprocess.run(cmd, cwd=str(REPO), env=child_env(),
                           stdout=lf, stderr=subprocess.STDOUT)
        say(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ◀ {rel} 退出码 {r.returncode}")
        return r.returncode


if __name__ == "__main__":
    sys.exit(main())
