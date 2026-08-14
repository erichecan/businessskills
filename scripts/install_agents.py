#!/usr/bin/env python3
"""重写全部 launchd plist，让它们统一走 launchd_runner.py（Homebrew python）。

为什么要统一：见 launchd_runner.py 的文件头。一句话——TCC 授权只需要点一次，
而且要点的那个目标是 .app 包（面板里永远可选），不是会被置灰的符号链接。

用法：
  python3 install_agents.py --dry-run   # 只打印会生成什么
  python3 install_agents.py             # 写入 ~/Library/LaunchAgents 并重新加载
  python3 install_agents.py --verify    # 只检查当前 7 个任务的退出码
"""
import argparse
import plistlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "launchd_runner.py"
PYTHON = "/opt/homebrew/bin/python3"
AGENTS = Path.home() / "Library" / "LaunchAgents"

# label → (相对 scripts/ 的脚本, 参数, 触发时点)
# 时点全部保持原样，这次只换解释器和入口，不动调度 —— 一次只改一件事，
# 出问题才分得清是权限没生效还是时点改错了。
JOBS = {
    "com.eric.xhsprobe":   ("xhs-probe/daily_probe.sh", [],
                            [(0, 15), (6, 15), (12, 15), (18, 15)]),
    "com.eric.xhsaudit":   ("xhs-health/independent_audit.py", [], [(9, 5)]),
    "com.eric.xhshealth":  ("xhs-health/health_check.py", [], [(9, 30), (19, 30)]),
    "com.eric.xhsdata":    ("xhs-publish/daily_data.sh", [], [(8, 30)]),
    # 一天四批，每批 返工2 + 新稿2 = 4 篇，理论 16 篇/天。
    # 时点按 Claude 的 5 小时额度窗口切：额度 4am 重置，之后每 5 小时一个窗口，
    # 每个窗口开头半小时开跑，一批只吃一个窗口的额度，不会把后面几批饿死。
    # 返工排在新稿前面且占一半：今天实测返工 3 篇全过线（84→89 / 82→88 / 76→87），
    # 其中两篇只用 1 轮 ≈ 2 次 claude 调用，而新稿要跑满 3 轮 ≈ 6 次。
    # 同样的额度，返工的产出效率是新稿的三倍。
    "com.eric.xhswrite":   ("xhs-loop/refine_loop.py", ["--rework", "2", "--count", "2"],
                            [(4, 30), (9, 30), (14, 30), (19, 30)]),
    "com.eric.xhspublish": ("xhs-publish/auto_publish.py", [], [(9, 0), (14, 0), (21, 0)]),
    "com.eric.xhsbrief":   ("xhs-health/nightly_brief.py", [], [(21, 0)]),
}


# ⛔ 2026-08-13：日志不再落 /tmp。macOS 会定期清理 /tmp，2026-08-13 查 probe
# 为什么连挂三轮时，7 个任务的日志**一个都不在**，只能靠翻探测原始 JSON 里的
# _error 才找出根因。日志得比它记录的事故活得久。
# 放 ~/Library/Logs/xhs/：本机盘（外置卷没挂载时也写得进去）、macOS 标准位置、
# Console.app 直接可看。这里是 plist 层的兜底文件；每次运行的完整输出由
# launchd_runner.py 另写按天切分的 <task>-YYYY-MM-DD.log。
LOG_DIR = Path.home() / "Library" / "Logs" / "xhs"


def build(label, rel, extra, times):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = str(LOG_DIR / f"launchd-{label}.log")
    return {
        "Label": label,
        "ProgramArguments": [PYTHON, str(RUNNER), rel, *extra],
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in times],
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def existing_times(label):
    """沿用磁盘上已有的时点，别把用户手调过的时间冲掉。"""
    p = AGENTS / f"{label}.plist"
    if not p.exists():
        return None
    try:
        d = plistlib.loads(p.read_bytes())
    except Exception:
        return None
    sci = d.get("StartCalendarInterval")
    if isinstance(sci, dict):
        sci = [sci]
    if not isinstance(sci, list):
        return None
    return [(e.get("Hour", 0), e.get("Minute", 0)) for e in sci] or None


def verify():
    bad = 0
    for label in JOBS:
        out = subprocess.run(["launchctl", "list", label],
                             capture_output=True, text=True).stdout
        m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', out)
        if not out.strip():
            print(f"  ❌ {label} 未加载")
            bad += 1
            continue
        raw = int(m.group(1)) if m else -1
        rc = raw >> 8 if raw > 255 else raw
        hint = "  ← 外置卷权限未生效（TCC）" if rc in (2, 126) else ""
        print(f"  {'✅' if rc == 0 else '❌'} {label:<22} 退出码 {rc}{hint}")
        bad += rc != 0
    print(f"\n{len(JOBS)-bad}/{len(JOBS)} 正常")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--reset-times", nargs="*", metavar="LABEL",
                    help="用 JOBS 里的时点覆盖磁盘上已有的。可跟 label 子串只覆盖指定任务，"
                         "不跟参数则覆盖全部。默认沿用磁盘上的时点，不冲掉手调过的")
    args = ap.parse_args()

    if args.verify:
        return verify()

    if not Path(PYTHON).exists():
        print(f"⛔ 找不到 {PYTHON}（brew install python@3.14）", file=sys.stderr)
        return 1

    AGENTS.mkdir(parents=True, exist_ok=True)
    for label, (rel, extra, times) in JOBS.items():
        reset = args.reset_times is not None and (
            not args.reset_times or any(s in label for s in args.reset_times))
        times = times if reset else (existing_times(label) or times)
        data = build(label, rel, extra, times)
        path = AGENTS / f"{label}.plist"
        when = " ".join(f"{h:02d}:{m:02d}" for h, m in times)
        print(f"{label:<22} {rel} {' '.join(extra)}  @ {when}")
        if args.dry_run:
            continue
        path.write_bytes(plistlib.dumps(data))
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], capture_output=True)
    if args.dry_run:
        print("\n[dry-run] 未写入")
        return 0
    print(f"\n✅ 已重装 {len(JOBS)} 个任务，Program 统一为 {PYTHON}")
    print("   2026-08-05 实测：换成 Homebrew python 后外置卷读得到了，"
          "不需要任何「完全磁盘访问权限」授权。")
    print("   验证：python3 scripts/install_agents.py --verify")
    print("   真跑一次：launchctl kickstart -k gui/$(id -u)/com.eric.xhsbrief")
    return 0


if __name__ == "__main__":
    sys.exit(main())
