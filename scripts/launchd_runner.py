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
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent


def main():
    if len(sys.argv) < 2:
        print("用法：launchd_runner.py <scripts/ 下的相对路径> [参数...]", file=sys.stderr)
        return 2
    rel, rest = sys.argv[1], sys.argv[2:]
    target = SCRIPTS / rel
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not target.exists():
        print(f"[{stamp}] ⛔ 找不到 {target}", file=sys.stderr)
        return 2

    # 外置卷没挂载时报错要说人话。原先的 "Operation not permitted" 让人以为是权限，
    # 其实盘拔了也是同一类症状，两者得分得开。
    if not REPO.exists():
        print(f"[{stamp}] ⛔ 仓库路径不可达：{REPO}（外置卷未挂载？）", file=sys.stderr)
        return 2

    cmd = (["/bin/bash", str(target)] if target.suffix == ".sh"
           else [sys.executable, str(target)]) + rest
    print(f"[{stamp}] ▶ {rel} {' '.join(rest)}", flush=True)
    # cwd 固定在仓库根：好几个脚本用相对路径读 xhs/素材库，
    # launchd 起的进程 cwd 是 / ，不设的话它们会去 /xhs/素材库 找。
    r = subprocess.run(cmd, cwd=str(REPO), env={**os.environ, "PYTHONUNBUFFERED": "1"})
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ◀ {rel} 退出码 {r.returncode}", flush=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
