#!/usr/bin/env python3
"""素材库健康检查（进程外心跳）— P0 止血。

检查三件事：
1. 运行日志断流：最新 xlsx 产出日期 与 运行日志.csv 最后一行日期 相差 >1 天 → 告警
2. 日志 schema：最近数据行必须恰好 10 列，数值列必须是数字 → 违规即告警
3. 打分闸门：最近 2 天的 成稿_*.md 必须在 审核记录.csv 有对应行 → 缺失即告警

告警动作：macOS 通知 + 追加写 素材库/健康告警.md + 退出码 1。
由 com.eric.xhshealth LaunchAgent 每天 09:30 触发，也可手动运行。
"""
import csv
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
RUN_LOG = SUCAI / "运行日志.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
ALERT_FILE = SUCAI / "健康告警.md"

LOG_COLUMNS = 10
NUMERIC_COLS = [2, 3, 4, 5, 7]  # 跑的关键词数/总抓取条数/本轮新增/记忆库累计/连续0新增轮数

EXPECTED_RUNS = 4            # 采集任务每 6 小时一轮
MIN_QUOTES_PER_RUN = 2       # 每轮至少收 2 条评论区原话（成稿可信度维度的唯一合法来源）
MAX_CANDIDATE_BACKLOG = 200  # 候选词积压上限

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def latest_xlsx_date():
    dates = []
    for f in SUCAI.glob("职场表达与面试技巧_*.xlsx"):
        m = DATE_RE.search(f.name)
        if m:
            dates.append(date.fromisoformat(m.group(1)))
    return max(dates) if dates else None


def check_log_freshness(alerts):
    xlsx_date = latest_xlsx_date()
    if xlsx_date is None:
        alerts.append("找不到任何 xlsx 产出——采集任务可能已整体停摆")
        return
    if not RUN_LOG.exists():
        alerts.append("运行日志.csv 不存在")
        return
    log_dates = []
    for row in csv.reader(RUN_LOG.open(encoding="utf-8-sig")):
        if row and DATE_RE.fullmatch(row[0].strip()):
            log_dates.append(date.fromisoformat(row[0].strip()))
    if not log_dates:
        alerts.append("运行日志.csv 没有任何有效日期行")
        return
    gap = (xlsx_date - max(log_dates)).days
    if gap > 1:
        alerts.append(
            f"运行日志断流：任务仍在产出（最新 xlsx {xlsx_date}），"
            f"但日志停在 {max(log_dates)}，已断 {gap} 天"
        )


def check_log_schema(alerts):
    if not RUN_LOG.exists():
        return
    rows = [r for r in csv.reader(RUN_LOG.open(encoding="utf-8-sig")) if r]
    bad = []
    for i, row in enumerate(rows[-10:], start=len(rows) - min(10, len(rows) - 1)):
        if not DATE_RE.fullmatch(row[0].strip()):
            continue  # 表头或注释行
        if len(row) != LOG_COLUMNS:
            bad.append(f"第{i+1}行 {row[0]}/{row[1] if len(row)>1 else '?'}：{len(row)} 列（应为 {LOG_COLUMNS}）")
            continue
        for c in NUMERIC_COLS:
            if row[c].strip() and not re.fullmatch(r"\d+", row[c].strip()):
                bad.append(f"第{i+1}行 {row[0]}：第{c+1}列应为数字，实为「{row[c][:20]}」")
                break
    if bad:
        alerts.append("运行日志 schema 违规：\n  - " + "\n  - ".join(bad))


def check_audit_gate(alerts):
    cutoff = date.today() - timedelta(days=2)
    recent = []
    for f in SUCAI.glob("成稿_*.md"):
        m = DATE_RE.search(f.name)
        if m and date.fromisoformat(m.group(1)) >= cutoff:
            recent.append(f.name)
    if not recent:
        return
    # 闸门只认独立审核。自评行不算过闸——自评与独立审核实测差 19 分且处置相反
    # （08-01 稿：自评 87 绿「发布」，独立审核 68 红「返工」）。
    audited, self_only = set(), set()
    if AUDIT_LOG.exists():
        for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig")):
            name = (row.get("成稿文件") or "").strip()
            if (row.get("审核方") or "").strip() == "独立审核":
                audited.add(name)
            else:
                self_only.add(name)
    missing = [f for f in recent if f not in audited]
    if missing:
        detail = []
        for f in missing:
            mark = "（仅有自评，不算过闸）" if f in self_only else "（无任何审核记录）"
            detail.append(f + mark)
        alerts.append(
            "成稿未过审核闸门（需独立审核行）：\n  - " + "\n  - ".join(detail)
        )


def check_publish_backfill(alerts):
    """词库的发布回流是否断了。

    L3 只盯一个指标：搜索进入占比（20260731 决策 4）。它必须发布后从笔记后台回填，
    没有任何自动化能凭空造出来。这里管两件事：
    1. 标了已发布却没有笔记链接 → 记录不实（2026-08-02 就出过：标着「已使用/发布日08-01」
       实际一篇没发，是成稿被当成了发布）
    2. 发布满 7 天还没回填占比 → 该去后台取数了
    """
    ciku = SUCAI / "词库.csv"
    if not ciku.exists():
        return
    rows = list(csv.DictReader(ciku.open(encoding="utf-8-sig")))
    if not rows or "笔记链接" not in rows[0]:
        return
    unlinked, due = [], []
    today = date.today()
    for r in rows:
        kw = (r.get("关键词") or "").strip()
        status = (r.get("状态") or "").strip()
        pub = (r.get("发布日") or "").strip()
        link = (r.get("笔记链接") or "").strip()
        ratio = (r.get("搜索来源占比") or "").strip()
        if status == "已发布" and not link:
            # ⛔ 2026-08-13（Eric 定）：只对**发布满 7 天**仍没链接的报。
            # 刚发的、以及定时还没到点的笔记本来就没有链接 ——「定时发布」那一刻笔记
            # 还没出去，noteId 得等 fetch_stats 事后从创作后台列表里捞回来。
            # 不设门槛的话每天新发的稿都会立刻进名单，名单长期挂着一串「其实没问题」的词，
            # 真正补不上的那几个反而被淹掉（2026-08-13 的告警里 6 条正是这样）。
            #
            # ⚠️ 只推迟「报」，没有推迟「补」—— backfill_note_links 仍旧每天跑，是故意的：
            # 创作后台列表页只显示最近几天，等满 7 天再去捞就永远捞不到了；
            # 而 aged_candidates 反过来又要靠笔记链接才能开单篇详情页（fetch_stats.py:173），
            # 补晚了整条预测闭环就断死。补要趁早，报要延后，两件事的时点本就不该相同。
            try:
                aged = pub and (today - date.fromisoformat(pub)).days >= 7
            except ValueError:
                aged = True          # 发布日格式非法，下面那条分支会单独点名
            if aged or not pub:      # 没有发布日却标着已发布，本身就是记录不实
                unlinked.append(kw if pub else f"{kw}（标已发布却没有发布日）")
        if pub and not ratio:
            try:
                if (today - date.fromisoformat(pub)).days >= 7:
                    due.append(f"{kw}（发布于 {pub}）")
            except ValueError:
                unlinked.append(f"{kw}：发布日「{pub}」格式非法")
    if unlinked:
        alerts.append("词库记录不实（标已发布但无笔记链接）：\n  - " + "\n  - ".join(unlinked))
    if due:
        alerts.append("发布满 7 天未回填搜索来源占比：\n  - " + "\n  - ".join(due))


def check_verdict_conflict(alerts):
    """同一篇稿的自评处置与独立审核处置打架 → 暴露出来。

    处置以独立审核为准。留着两行矛盾记录不告警的话，看错行就会把该返工的稿发出去。
    """
    if not AUDIT_LOG.exists():
        return
    by_draft = {}
    for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig")):
        name = (row.get("成稿文件") or "").strip()
        by_draft.setdefault(name, {})[(row.get("审核方") or "").strip()] = (
            (row.get("处置") or "").strip(), (row.get("总分") or "").strip())
    conflicts = []
    for name, sides in by_draft.items():
        indep, own = sides.get("独立审核"), sides.get("自评")
        if indep and own and indep[0] != own[0]:
            conflicts.append(f"{name}：自评 {own[1]}分→{own[0]}，独立审核 {indep[1]}分→{indep[0]}（以独立审核为准）")
    if conflicts:
        alerts.append("处置冲突（自评与独立审核不一致）：\n  - " + "\n  - ".join(conflicts))


def check_run_completeness(alerts):
    """昨天的采集轮次齐不齐。

    采集任务每 6 小时一轮（0/6/12/18 点）＝ 一天四轮。只查昨天不查今天——
    健康检查 09:30 跑的时候，今天才轮到第二轮，查了必然误报。
    这一条补的是 check_log_freshness 的盲区：那条只看「有没有断流」，
    一天只跑一轮也算新鲜，但实际漏了 3/4 的采集量（08-01 就只有 run1–run3）。
    """
    if not RUN_LOG.exists():
        return
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    runs = {r[1].strip() for r in csv.reader(RUN_LOG.open(encoding="utf-8-sig"))
            if len(r) > 1 and r[0].strip() == yesterday}
    if not runs:
        alerts.append(f"采集轮次缺失：{yesterday} 运行日志一轮都没有")
        return
    missing = [f"run{i}" for i in range(1, EXPECTED_RUNS + 1) if f"run{i}" not in runs]
    if missing:
        alerts.append(f"采集轮次不全：{yesterday} 只跑了 {len(runs)}/{EXPECTED_RUNS} 轮，缺 {'/'.join(missing)}")


def check_quote_harvest(alerts):
    """评论区原话收割配额。

    原话是成稿可信度维度（15 分）的唯一合法来源，也是案例库的供给源
    （harvest_cases.py 从这里提候选）。收割断供，成稿就只能靠脚本化改写，
    审核必然扣「引语只有一条真人原话」。
    ⚠️ 用 评论区原话.csv 的实际行数核对，不读运行日志备注里模型自报的「收割 N 条」。
    """
    quotes = SUCAI / "评论区原话.csv"
    if not quotes.exists() or not RUN_LOG.exists():
        return
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    runs = sum(1 for r in csv.reader(RUN_LOG.open(encoding="utf-8-sig"))
               if len(r) > 1 and r[0].strip() == yesterday)
    if not runs:
        return  # 一轮没跑，由 check_run_completeness 报，这里不重复告警
    got = sum(1 for r in csv.DictReader(quotes.open(encoding="utf-8-sig"))
              if (r.get("日期") or "").strip() == yesterday)
    quota = runs * MIN_QUOTES_PER_RUN
    if got < quota:
        alerts.append(f"评论区原话欠收：{yesterday} 跑了 {runs} 轮只收 {got} 条"
                      f"（配额 {MIN_QUOTES_PER_RUN} 条/轮 = {quota} 条）")


def check_prediction_review(alerts):
    """有够 7 天数据但没复盘的预测 → 告警。

    预测本身没价值，预测-实际的差值才有价值。发了不复盘等于白押数，
    模型系数永远停在先验值上（docs/20260803-小红书数据预测调研.md 第五节）。
    """
    script = REPO / "scripts" / "xhs-loop" / "review_prediction.py"
    if not script.exists() or not (SUCAI / "预测记录.csv").exists():
        return
    try:
        r = subprocess.run([sys.executable, str(script), "--list"],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        alerts.append(f"预测复盘检查无法执行：{e}")
        return
    m = re.search(r"可复盘 (\d+) 篇", r.stdout or "")
    if m and int(m.group(1)) > 0:
        alerts.append(f"有 {m.group(1)} 篇预测够 7 天数据但未复盘："
                      f"跑 python3 scripts/xhs-loop/review_prediction.py")


def check_candidate_backlog(alerts):
    """候选词积压。

    候选词只进不出的话，探测和选题都会被稀释——真正值得做的词淹在几百个里选不出来。
    运行日志自己也在提醒（08-02 run4：「候选积压达674个，远超30阈值」），
    但提醒写在备注里没人处理，改成机械告警。
    """
    pool = SUCAI / "关键词池.csv"
    if not pool.exists():
        return
    n = sum(1 for r in csv.DictReader(pool.open(encoding="utf-8-sig"))
            if (r.get("类型") or "").strip() == "候选")
    if n > MAX_CANDIDATE_BACKLOG:
        alerts.append(f"候选词积压 {n} 个（阈值 {MAX_CANDIDATE_BACKLOG}）：该做一次退休清点，"
                      f"否则值得做的词会淹在里面选不出来")


def notify(title, text):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text[:180]}" with title "{title}" sound name "Basso"'],
            check=False, timeout=10,
        )
    except Exception:
        pass


def check_draft_quality(alerts):
    try:
        r = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "draft_check.py")],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            alerts.append("成稿机械及格线违规：\n" + r.stdout.strip())
    except Exception as e:
        alerts.append(f"成稿机械检查无法执行：{e}")


def main() -> int:
    alerts = []
    check_log_freshness(alerts)
    check_run_completeness(alerts)
    check_quote_harvest(alerts)
    check_candidate_backlog(alerts)
    check_prediction_review(alerts)
    check_log_schema(alerts)
    check_audit_gate(alerts)
    check_verdict_conflict(alerts)
    check_publish_backfill(alerts)
    check_draft_quality(alerts)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not alerts:
        print(f"[{now}] 健康检查通过：日志新鲜、schema 合规、成稿均有审核记录")
        return EXIT_OK

    report = f"\n## {now} 健康检查告警\n\n" + "\n\n".join(f"- {a}" for a in alerts) + "\n"
    with ALERT_FILE.open("a", encoding="utf-8") as f:
        if ALERT_FILE.stat().st_size == 0:
            f.write("# 素材库健康告警记录\n\n> 由 scripts/xhs-health/health_check.py 自动写入。处理完一条就删一条。\n")
        f.write(report)
    print(f"[{now}] 发现 {len(alerts)} 项告警：")
    for a in alerts:
        print(f"  ⛔ {a}")
    notify("小红书采集任务告警", f"{len(alerts)} 项异常，详见 素材库/健康告警.md")
    return EXIT_ALERTS


# 退出码语义（2026-08-13 定）。
# 起因：brief 天天报「❌ 健康检查 — 上次退出码 1」，看着像脚本坏了，
# 实际是它**正常干完活并且发现了 4 项告警**。监控自己被误判成故障，
# 结果是所有绿灯都不可信 —— 人会开始忽略这一栏，那监控就白建了。
# 「检查跑失败」和「检查跑成功但有问题」是两回事，退出码必须分得开：
EXIT_OK = 0        # 检查跑完，没有告警
EXIT_FAILED = 1    # 检查**本身**没跑成（异常、文件读不了）—— 这才是要救的
EXIT_ALERTS = 3    # 检查跑完了，发现 N 项告警 —— 任务是健康的，内容需要人管
                   # 用 3 不用 2：2 已被 launchd_runner 占作「找不到脚本/外置卷未挂载」


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                   # noqa: BLE001
        # 兜底：任何未捕获异常都必须落成 EXIT_FAILED，不能让 Python 默认的 1
        # 和「有告警」的 1 混在一起 —— 那正是之前分不清的原因。
        import traceback
        traceback.print_exc()
        print(f"⛔ 健康检查本身失败：{e}", file=sys.stderr)
        sys.exit(EXIT_FAILED)
