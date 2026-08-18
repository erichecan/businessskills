#!/usr/bin/env python3
"""定时任务心跳判据的守门测试 —— 不花额度，纯本地。

为什么必须有：心跳的价值全在「报得准」。漏报（判成健康）会让失联继续静默；
**误报比漏报更糟** —— 假警报多了这个 section 就会被跳过，那就绕回「没人看」，
而「没人看」正是它要解决的问题本身。

改完 nightly_brief.section_heartbeat / _last_run 一定要跑一次：
    python3 scripts/test_heartbeat.py
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "xhs-health"))
import nightly_brief as NB  # noqa: E402

NOW = datetime.now()


def _t(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


# (场景, 日志内容, 期望健康?)
CASES = [
    ("正常一轮", [f"▶ {_t(2)} EDT  daily", "◀ 退出码 0 · x"], True),
    # 「发布必保」这条线的典型失败：Chrome 没起 / 登录态掉了 / 页面改版。
    # runner 照样被拉起、照样写 ▶，然后非 0 退出 —— 只看 ▶ 会判成健康。
    ("跑了但失败（退出码 1）", [f"▶ {_t(2)} EDT  daily", "◀ 退出码 1 · x"], False),
    ("中途被杀（有 ▶ 无 ◀）", [f"▶ {_t(2)} EDT  daily"], False),
    # ⚠️ 最容易写错的一条：21:00 之外还有 22:30 / 23:45 两次补射，
    # 「21:00 失败、22:30 成功」是**正常且预期**的形态，判据只能看最后一次。
    # 按「当天有过失败」报警的话，每次补射生效都会收到一条假警报。
    ("补射：21 点失败 → 22 点成功", [f"▶ {_t(5)} EDT  daily", "◀ 退出码 1 · x",
                                    f"▶ {_t(3)} EDT  daily", "◀ 退出码 0 · x"], True),
    ("补射也全失败", [f"▶ {_t(5)} EDT  daily", "◀ 退出码 1 · x",
                     f"▶ {_t(3)} EDT  daily", "◀ 退出码 2 · x"], False),
    ("超过 24h 没跑", [f"▶ {_t(30)} EDT  daily", "◀ 退出码 0 · x"], False),
]


def main():
    fails = []
    print(f"{'场景':<28}{'期望':>6}{'实际':>6}")
    saved = NB.FOREIGN_HEARTBEATS
    for name, body, expect in CASES:
        f = Path(tempfile.mktemp(suffix=".log"))
        f.write_text("\n".join(body) + "\n", encoding="utf-8")
        NB.FOREIGN_HEARTBEATS = {"测试线": (f, "com.eric.none.test", 24)}
        try:
            _, ok = NB.section_heartbeat()
        finally:
            f.unlink(missing_ok=True)
        mark = "✅" if ok == expect else "⛔"
        if ok != expect:
            fails.append(name)
        print(f"{name:<28}{'健康' if expect else '报警':>6}{'健康' if ok else '报警':>6}  {mark}")
    NB.FOREIGN_HEARTBEATS = saved

    # 读不到日志时必须静默跳过 —— 隔壁项目改了路径不该拖垮整份 brief。
    NB.FOREIGN_HEARTBEATS = {"不存在": (Path("/nonexistent/x.log"), "com.eric.none.test", 24)}
    _, ok = NB.section_heartbeat()
    NB.FOREIGN_HEARTBEATS = saved
    print(f"{'日志不存在应静默跳过':<28}{'健康':>6}{'健康' if ok else '报警':>6}  {'✅' if ok else '⛔'}")
    if not ok:
        fails.append("日志不存在应静默跳过")

    print("\n" + ("✅ 全部通过" if not fails else f"⛔ 失败 {len(fails)} 项：{'、'.join(fails)}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
