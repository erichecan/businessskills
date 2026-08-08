"""接力循环的离线验证 —— 不碰浏览器、不写真日志。

覆盖四种走向：全部发完 / 中途超时停手 / tab 关掉停手 / --no-relay 退回旧行为。
重点验证一条不变量：**没确认发出去的稿，绝不被记成已发布，也绝不预填下一篇**。
"""
import sys
sys.path.insert(0, "/Volumes/datacenter/04-eric/AIcoding/businessskills/scripts/xhs-publish")
sys.path.insert(0, "/Volumes/datacenter/04-eric/AIcoding/businessskills/scripts/case-entry")
import auto_publish as ap

DRAFTS = ["A.md", "B.md", "C.md", "D.md"]


def setup(watch_results):
    calls = {"prefill": [], "logged": [], "recorded": []}
    ap.candidates = lambda: [(n, True, "测试放行") for n in DRAFTS]
    ap.published_today = lambda: 0
    ap.log_run = lambda row: calls["logged"].append((row.get("成稿文件"), row.get("发布")))

    def fake_publish_one(name, dry_run, immediate=False, full_auto=False):
        calls["prefill"].append(name)
        return 0, f"tid-{name}"

    def fake_watch(tid, name, **kw):
        return watch_results.pop(0)

    def fake_record(name, at, how="接力检测"):
        calls["recorded"].append((name, at))
        return "已回填词库"

    ap.publish_one = fake_publish_one
    ap.watch_until_published = fake_watch
    ap.record_published = fake_record
    return calls


def run(argv):
    sys.argv = ["auto_publish.py"] + argv
    return ap.main()


PUB = lambda t: {"state": "published", "sched": t, "why": ""}

# 1. 一路顺畅：配额 3 篇，一次运行全部走完
calls = setup([PUB("2026-08-07 09:00"), PUB("2026-08-07 11:00")])
run([])
assert calls["prefill"] == ["A.md", "B.md", "C.md"], calls["prefill"]
assert calls["recorded"] == [("A.md", "2026-08-07 09:00"), ("B.md", "2026-08-07 11:00")], calls["recorded"]
print("✅ 顺畅：预填 3 篇（=DAILY_QUOTA），前 2 篇确认后各记一次账，最后一篇留给人点")

# 2. 第二篇等超时：必须停手，不预填第三篇，且只记 ⏸ 不记 ✅
calls = setup([PUB("2026-08-07 09:00"), {"state": "timeout", "sched": "", "why": "等了 45 分钟"}])
run([])
assert calls["prefill"] == ["A.md", "B.md"], calls["prefill"]
assert calls["recorded"] == [("A.md", "2026-08-07 09:00")], calls["recorded"]
assert calls["logged"][-1] == ("B.md", "⏸ 接力未确认（timeout）"), calls["logged"][-1]
print("✅ 超时：停在第 2 篇，第 3 篇没被预填（否则会盖掉还没发的第 2 篇）；B 记 ⏸ 未记 ✅")

# 3. tab 被关掉：同样停手，不猜「大概发了吧」
calls = setup([{"state": "lost", "sched": "", "why": "连续 3 次读不到发布页"}])
run([])
assert calls["prefill"] == ["A.md"], calls["prefill"]
assert calls["recorded"] == [], calls["recorded"]
assert calls["logged"][-1] == ("A.md", "⏸ 接力未确认（lost）")
print("✅ tab 关掉：停手，一条 ✅ 都没记 —— 宁可漏判，不可错判")

# 4. --no-relay：退回旧行为，填一篇就停
calls = setup([])
run(["--no-relay"])
assert calls["prefill"] == ["A.md"], calls["prefill"]
assert calls["recorded"] == []
print("✅ --no-relay：只预填 1 篇就停，与 2026-08-06 之前行为一致")

print("\n全部通过。")
