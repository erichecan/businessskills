#!/usr/bin/env python3
"""额度分类与三个调用点退避策略的回归测试。

跑法：python3 scripts/test_limit_handling.py

这个测试存在的理由是 2026-08-10 那次事故：全天 112 次调用、0 token、0 产出。
根因是 weekly limit 被当成 session limit，每次都熬满 5 小时。
下面每条用例的输入都是 **loop日志/ 里的真实原文**，不是我编的。
"""
import sys
import types
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from claude_limits import (RATE, SESSION, WEEKLY, classify_limit,  # noqa: E402
                           is_limit)

# ── 真实样本（grep 自 xhs/素材库/loop日志/ 与 ~/.claude/projects/）──────────
REAL_WEEKLY = [
    "You've hit your weekly limit · resets 12am (America/Toronto)",
    "You've hit your weekly limit · resets Aug 11 at 12am (America/Toronto)",
]
REAL_SESSION = [
    "You've hit your session limit · resets 4am (America/Toronto)",
    "You've hit your session limit · resets 10:50am (America/Toronto)",
    "You've hit your session limit · resets 9pm (America/Toronto)",
]
NOT_LIMIT = [
    "",
    "===SLUG===\n谈薪被压价\n===MARKDOWN===\n# 正文……",
    '{"disposition": "做", "density_echo": "高"}',
    "Error: ENOENT: no such file or directory",
]

failures = []


def check(label, got, want):
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}\n     期望 {want!r}，实际 {got!r}")
        failures.append(label)


print("── 1. 额度分类 ──")
for s in REAL_WEEKLY:
    check(f"weekly: {s[:46]}…", classify_limit(s), WEEKLY)
for s in REAL_SESSION:
    check(f"session: {s[:46]}…", classify_limit(s), SESSION)
check("429 归 RATE", classify_limit("API Error 429 Too Many Requests"), RATE)
check("overloaded 归 RATE", classify_limit("overloaded_error"), RATE)
for s in NOT_LIMIT:
    check(f"非额度: {(s[:30] or '(空)')}…", classify_limit(s), None)

print("\n── 2. 优先级：weekly 必须压过 session 与泛匹配 ──")
# ⛔ 这是整个修复的核心。两种提示都带 "resets N am"，
# 泛匹配若排在前面就会把 weekly 判成 session，于是继续熬 5 小时。
check("同时出现 session+weekly → weekly（停手）",
      classify_limit(REAL_SESSION[0] + "\n" + REAL_WEEKLY[0]), WEEKLY)
check("weekly 在 stderr、stdout 是正常输出 → weekly",
      classify_limit("正常输出", REAL_WEEKLY[0]), WEEKLY)
check("只说 resets 不说窗口 → 保守判 SESSION（继续等）",
      classify_limit("Usage limit reached · resets 3am"), SESSION)

print("\n── 3. 向后兼容：旧 LIMIT_RE 认得的都还认得 ──")
for s in REAL_WEEKLY + REAL_SESSION:
    check(f"is_limit: {s[:40]}…", is_limit(s), True)


# ── 4. 集成：三个调用点撞 weekly 时必须零 sleep ────────────────────────────
def _fake_completed(stdout):
    return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def probe_call_site(module_path, func_name, call, stdout):
    """加载模块，把 subprocess.run 和 time.sleep 都换成假的，返回 sleep 调用次数。"""
    sys.path.insert(0, str((SCRIPTS / module_path).parent))
    import importlib
    mod = importlib.import_module(Path(module_path).stem)
    slept = []
    with mock.patch.object(mod.subprocess, "run",
                           return_value=_fake_completed(stdout)), \
         mock.patch.object(mod.time, "sleep", side_effect=lambda s: slept.append(s)):
        try:
            call(getattr(mod, func_name))
        except SystemExit:
            pass
    return slept


print("\n── 4. 撞 weekly 时三个调用点都必须立刻返回（sleep 零次）──")

CASES = [
    ("xhs-loop/refine_loop.py", "run_claude", lambda f: f("prompt", "write_r1")),
    ("xhs-health/independent_audit.py", "run_claude_waiting_out_limits",
     lambda f: f("prompt")),
    ("xhs-probe/auto_analyze.py", "run_claude", lambda f: f("prompt", "probe_x")),
]

for path, fn, call in CASES:
    try:
        slept = probe_call_site(path, fn, call, REAL_WEEKLY[0])
        check(f"{Path(path).stem}.{fn} 撞 weekly → sleep {len(slept)} 次", len(slept), 0)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {path} 加载/调用失败：{type(e).__name__}: {e}")
        failures.append(path)

print("\n── 5. 撞 session 时仍要等（回归：别把该等的也停了）──")
for path, fn, call in CASES:
    try:
        slept = probe_call_site(path, fn, call, REAL_SESSION[0])
        ok = len(slept) > 0
        check(f"{Path(path).stem}.{fn} 撞 session → 有等待", ok, True)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {path}: {type(e).__name__}: {e}")
        failures.append(path)

print()
if failures:
    print(f"⛔ {len(failures)} 条未通过：{failures}")
    sys.exit(1)
print("✅ 全部通过")
