#!/usr/bin/env python3
"""半自动发布 — 闸门通过的成稿自动预填 + 打开定时开关，最后一步留给人。

为什么是半自动：小红书发布页的日历组件（d-datepicker）只认 isTrusted 的原生手势，
JS 合成 click、完整鼠标事件序列、CDP Input.dispatchMouseEvent 全部试过，
元素能点中但面板不展开。选时段这一下没法程序化，索性交给人——每篇约 10 秒。

复用 scripts/case-entry/case_entry.py 的 prefill_xhs / do_publish_click，不重写发布流程。

⛔ 四道闸门，全部满足才发（任一不满足 → 跳过并记日志，绝不发）：
  1. 审核记录里该稿有「独立审核」行，且最新处置 = 发布
     （或有「人工放行」行 —— 见 --approve）
  2. draft_check.py 机械及格线通过
  3. 该稿尚未发布过（发布日志里没有成功记录）
  4. 成品图目录有已渲染的卡片图

为什么要闸门：2026-08-02 之前自评有处置权，一篇独立审核 67 分该返工的稿挂着「发布」状态。
若那时已有自动发布，它会被发出去。闸门就是防这个。

用法：
  python3 auto_publish.py                      # 定时任务入口；每天最多发 DAILY_QUOTA 篇，时段轮换
  python3 auto_publish.py --dry-run            # 走到预填为止，不点发布
  python3 auto_publish.py --approve 成稿_x.md  # 人工放行一篇（写入审核记录，下次运行即可发）
  python3 auto_publish.py --list               # 只看闸门评估结果，不做任何操作
  python3 auto_publish.py --confirm 成稿_x.md --at "2026-08-03 09:00"
                                               # 你在页面上点完「定时发布」后跑这个，回填词库
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
CIKU = SUCAI / "词库.csv"
PUB_LOG = SUCAI / "发布日志.csv"
PROXY_BASE = "http://localhost:3456"
HEALTH_DIR = REPO / "scripts" / "xhs-health"

sys.path.insert(0, str(REPO / "scripts" / "case-entry"))

PUB_LOG_COLS = ["日期", "成稿文件", "标题", "闸门", "预填", "定时", "发布", "笔记链接", "备注"]

# 发布时段轮换池 — 每次发布取下一个，用来测出哪个时段搜索进入占比最高。
# 一轮 7 个点跑完，配合词库的「搜索来源占比」回填即可横向比较。
SLOT_HOURS = [9, 11, 12, 17, 18, 20, 22]
DAILY_QUOTA = 3          # 每天发 3 篇，不是一天占满 7 个时段
SCHED_LEAD = timedelta(minutes=30)   # 小红书定时发布需要提前量，太近会被拒
TRUSTED_AUDITORS = {"独立审核", "人工放行"}


def next_slot(now=None, skip=0):
    """按发布日志里上一次用过的时段，取池中下一个；该点今天已过就顺延到明天。
    严格保持轮换顺序——不因今天来不及就跳号，否则时段对比会有偏。"""
    now = now or datetime.now()
    used = []
    for r in read_csv(PUB_LOG):
        # 只有真发出去的才算占用时段：dry-run 与失败记录不能推进轮换，
        # 否则几次调试就把 7 个时段空耗完，时段对比的样本量会失衡。
        if not (r.get("发布") or "").startswith("✅"):
            continue
        s = (r.get("定时") or "").strip()
        if " " in s:
            try:
                used.append(int(s.split()[1].split(":")[0]))
            except ValueError:
                pass
    if used and used[-1] in SLOT_HOURS:
        hour = SLOT_HOURS[(SLOT_HOURS.index(used[-1]) + 1 + skip) % len(SLOT_HOURS)]
    else:
        # 还没发过：挑今天来得及的最早时段，别让第一篇白等到明天。
        # 之后就严格按池子顺序轮换，保证每个时段样本量一致。
        todo = [h for h in SLOT_HOURS
                if now.replace(hour=h, minute=0, second=0, microsecond=0) > now + SCHED_LEAD]
        hour = todo[skip % len(todo)] if todo else SLOT_HOURS[skip % len(SLOT_HOURS)]
    # 分钟固定整点：定时发布界面上真人只会选 9:00 这种整点，挑 9:47 反而不像人操作
    cand = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cand <= now + SCHED_LEAD:
        cand += timedelta(days=1)
    return cand.strftime("%Y-%m-%d %H:%M"), hour


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def published_today():
    today = date.today().isoformat()
    return sum(1 for r in read_csv(PUB_LOG)
               if r.get("发布", "").startswith("✅") and (r.get("日期") or "").startswith(today))


def published_already():
    return {r["成稿文件"].strip() for r in read_csv(PUB_LOG) if r.get("发布", "").startswith("✅")}


def gate(name):
    """返回 (是否放行, 理由)。理由无论放行与否都要能解释清楚。"""
    rows = [r for r in read_csv(AUDIT_LOG) if r.get("成稿文件", "").strip() == name]
    trusted = [r for r in rows if (r.get("审核方") or "").strip() in TRUSTED_AUDITORS]
    if not trusted:
        self_only = [r for r in rows if (r.get("审核方") or "").strip() == "自评"]
        return False, "只有自评、无独立审核" if self_only else "无任何审核记录"

    latest = trusted[-1]
    disposition = (latest.get("处置") or "").strip()
    if disposition != "发布":
        return False, f"{latest.get('审核方')}处置为「{disposition}」（需「发布」；可用 --approve 人工放行）"

    if name in published_already():
        return False, "发布日志显示已发布过"

    stem = name.removeprefix("成稿_").removesuffix(".md")
    imgs = sorted((SUCAI / "成品图" / stem).glob("*.png")) if (SUCAI / "成品图" / stem).is_dir() else []
    if not imgs:
        return False, "成品图目录没有已渲染的卡片图"

    r = subprocess.run([sys.executable, str(HEALTH_DIR / "draft_check.py"), "--days", "30"],
                       capture_output=True, text=True)
    if r.returncode != 0 and name in r.stdout:
        first = next((l.strip() for l in r.stdout.splitlines() if l.strip().startswith("-")), "见 draft_check 输出")
        return False, f"机械及格线未过：{first}"

    return True, f"{latest.get('审核方')} {latest.get('总分')}分 · 处置发布 · 机械项通过 · {len(imgs)} 张图"


def candidates():
    out = []
    for f in sorted(SUCAI.glob("成稿_*.md")):
        ok, why = gate(f.name)
        out.append((f.name, ok, why))
    return out


def log_run(row):
    exists = PUB_LOG.exists()
    with PUB_LOG.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PUB_LOG_COLS)
        if not exists:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in PUB_LOG_COLS})


def keyword_of(name):
    """从成稿正文头部取它写的是哪个词。成稿模板里「关键词来源：`词库.csv`「X」」是确定信息，
    比拿标题去猜可靠。2026-08-05 之前只按标题匹配，而标题策略早已改成「不复读搜索原句」
    （见 commit ee904ce），于是每篇都匹配不上、发布日和笔记链接一路空着没人发现。"""
    f = SUCAI / name
    if not f.exists():
        return ""
    head = f.read_text(encoding="utf-8")[:2000]
    m = re.search(r"关键词来源[^「]*「([^」]+)」", head)
    return m.group(1).strip() if m else ""


def backfill_ciku(title, link, name=""):
    """把发布日/笔记链接写回词库。先按成稿声明的关键词精确定位，取不到再退回标题包含匹配。"""
    rows = read_csv(CIKU)
    if not rows:
        return "词库为空"
    cols = list(rows[0].keys())
    hit = None
    declared = keyword_of(name) if name else ""
    if declared:
        hit = next((r for r in rows if (r.get("关键词") or "").strip() == declared), None)
    if hit is None:
        for r in rows:
            kw = (r.get("关键词") or "").strip()
            if kw and (kw in title or title in kw):
                hit = r
                break
    if hit is None:
        hint = f"；成稿声明的词「{declared}」不在词库里" if declared else ""
        return f"词库无匹配关键词（标题「{title}」{hint}），发布日未回填"
    hit["状态"] = "已发布"
    hit["发布日"] = date.today().isoformat()
    if link:
        hit["笔记链接"] = link
    # 原子替换：refine_loop 可能正并发读词库选题，直接 open("w") 会有一段
    # 文件被截断、只写了一半的窗口，读者这时读到的是残缺 CSV。
    tmp = CIKU.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, CIKU)
    return f"已回填词库「{hit['关键词']}」" + ("" if link else "（笔记链接待人工补）")


def approve(name):
    rows = read_csv(AUDIT_LOG)
    if not rows:
        print("审核记录为空，无法放行", file=sys.stderr)
        return 1
    cols = list(rows[0].keys())
    prev = [r for r in rows if r.get("成稿文件", "").strip() == name
            and (r.get("审核方") or "").strip() == "独立审核"]
    if not prev:
        print(f"⛔ {name} 没有独立审核记录，不允许人工放行——先跑 independent_audit.py", file=sys.stderr)
        return 1
    base = prev[-1]
    row = {c: "" for c in cols}
    row.update(base)
    row.update({"日期": date.today().isoformat(), "审核方": "人工放行", "处置": "发布",
                "备注": f"人工放行：在独立审核 {base.get('总分')}分（{base.get('评级')}）基础上由人决定发布"})
    with AUDIT_LOG.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=cols).writerow(row)
    print(f"✅ 已放行 {name}（基于独立审核 {base.get('总分')}分）。下次运行 auto_publish.py 即会发布。")
    return 0


SCHED_SWITCH_JS = (
    '(()=>{const w=document.querySelector(".post-time-wrapper");'
    'if(!w)return "无定时区";'
    'const on=()=>/(^|\\s)checked(\\s|$)/.test(w.querySelector(".d-switch-simulator")?.className||"");'
    'if(on())return "已开";'
    'const s=w.querySelector(".d-switch.d-clickable"),r=s.getBoundingClientRect();'
    's.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true,'
    'clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window}));'
    'return on()?"已开":"点击未生效";})()')


def open_sched_switch(tid):
    """只打开定时开关，不碰日历。滚动与点击必须分两次调用，否则 rect 还是滚动前的。"""
    import time as _t
    import urllib.request as rq

    def ev(js):
        req = rq.Request(f"{PROXY_BASE}/eval?target={tid}", data=js.encode(), method="POST")
        return json.loads(rq.urlopen(req, timeout=30).read()).get("value")

    ev('(()=>{const s=document.querySelector(".post-time-wrapper .d-switch.d-clickable");'
       'if(s)s.scrollIntoView({block:"center",behavior:"instant"});return 1})()')
    _t.sleep(2)
    ev(SCHED_SWITCH_JS)
    _t.sleep(1.5)
    return ev('(()=>{const w=document.querySelector(".post-time-wrapper");'
              'if(!w)return "无定时区";'
              'const on=/(^|\\s)checked(\\s|$)/.test(w.querySelector(".d-switch-simulator")?.className||"");'
              'return on?("已打开 · "+(w.innerText||"").replace(/\\n/g," ").slice(0,40)):"未打开";})()')


def publish_one(name, dry_run, immediate=False):
    """batch_offset：同一次运行里第 N 篇往后顺延 N 个时段，避免两篇撞同一个点。"""
    from case_entry import prefill_xhs, do_publish_click, parse_draft

    text = (SUCAI / name).read_text(encoding="utf-8")
    title = parse_draft(text)["title"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = {"日期": stamp, "成稿文件": name, "标题": title, "闸门": "✅ 通过"}

    pre = prefill_xhs(name, archived=False)
    row["预填"] = "✅" if pre.get("ok") else "❌"
    if not pre.get("ok"):
        row["发布"] = "— 未执行"
        row["备注"] = pre.get("log", "")[-160:].replace("\n", "；")
        log_run(row)
        print(f"⛔ 预填失败：\n{pre.get('log')}", file=sys.stderr)
        return 1

    print(pre["log"])
    sched_time, hour = next_slot(skip=publish_one.batch_offset)
    publish_one.batch_offset += 0 if immediate else 1
    row["定时"] = "" if immediate else sched_time

    if dry_run:
        row["发布"] = "— dry-run 未点发布"
        row["备注"] = f"预填完成；本次将用的时段是 {sched_time}（轮换池 {SLOT_HOURS}）"
        log_run(row)
        print(f"\n[dry-run] 已预填，未点发布。若继续将定时到 {sched_time}。")
        return 0

    if immediate:
        res = do_publish_click(pre["tid"], "now", "")
        row["发布"] = "✅ 已点击立即发布" if res.get("ok") else "❌ 点击失败"
        print(res.get("log", ""))
        row["备注"] = (backfill_ciku(title, "", name) if res.get("ok") else "") or ""
        log_run(row)
        return 0 if res.get("ok") else 1

    sw = open_sched_switch(pre["tid"])
    # 切到前台，否则这一步等于没做：cdp-proxy 用 background:true 建 tab，
    # 预填完的页面一直躲在后台，日志写着「已打开创作平台」而人根本看不见
    # （2026-08-05 实测，Eric 找不到页面，最后一步没人点，稿就卡在那）。
    focus = subprocess.run([sys.executable, str(Path(__file__).parent / "focus_tab.py"),
                            "--target", pre["tid"]], capture_output=True, text=True)
    print((focus.stdout or focus.stderr or "").strip())
    row["发布"] = "⏸ 待人工定时"
    row["备注"] = f"定时开关：{sw}"
    log_run(row)
    print(f"\n定时开关：{sw}")
    print("─" * 58)
    print(f"  请在 Chrome 里完成最后一步（约 10 秒）：")
    print(f"  1. 点时间那块，日历选日期")
    print(f"  2. 右侧两列选 {hour} 时 / 00 分")
    print(f"  3. 点底部红色「定时发布」按钮")
    print(f"  建议时段：{sched_time}（轮换池 {SLOT_HOURS}，本次第 {SLOT_HOURS.index(hour)+1} 个）")
    print(f"  发完回来跑：python3 scripts/xhs-publish/auto_publish.py \\")
    print(f"                --confirm \"{name}\" --at \"{sched_time}\"")
    print("─" * 58)
    return 0


def confirm(name, at):
    """人工在页面上点完「定时发布」后调用：记账 + 回填词库。"""
    from case_entry import parse_draft
    f = SUCAI / name
    if not f.exists():
        print(f"找不到 {f}", file=sys.stderr)
        return 1
    title = parse_draft(f.read_text(encoding="utf-8"))["title"]
    note = backfill_ciku(title, "", name)
    log_run({"日期": datetime.now().strftime("%Y-%m-%d %H:%M"), "成稿文件": name,
             "标题": title, "闸门": "✅ 通过", "预填": "✅", "定时": at,
             "发布": f"✅ 人工确认已定时 {at}", "备注": note})
    print(f"✅ 已记账：{name} → {at}")
    print(f"   {note}")
    print("   笔记链接请到创作后台复制后填进 词库.csv 的「笔记链接」列（health_check 会提醒）")
    return 0


publish_one.batch_offset = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", metavar="FILENAME")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--now", action="store_true", help="立即发布，不走时段轮换")
    ap.add_argument("--slots", action="store_true", help="只看下一个发布时段")
    ap.add_argument("--only", action="append", metavar="FILENAME",
                    help="只处理指定成稿（可重复）。手动发布用：绕过每日配额，但**不绕闸门**")
    ap.add_argument("--confirm", metavar="FILENAME", help="人工点完定时发布后回填")
    ap.add_argument("--at", metavar="TIME", default="", help="配合 --confirm，实际定时时间")
    args = ap.parse_args()

    if args.approve:
        return approve(args.approve)

    if args.confirm:
        return confirm(args.confirm, args.at or datetime.now().strftime("%Y-%m-%d %H:%M"))

    if args.slots:
        s, h = next_slot()
        print(f"轮换池：{SLOT_HOURS}")
        print(f"下一个时段：{s}（{h} 点）")
        return 0

    cands = candidates()
    passed = [(n, w) for n, ok, w in cands if ok]

    if args.list:
        print(f"{'成稿':<44}{'闸门':<6}理由")
        for n, ok, w in cands:
            print(f"{n[:42]:<44}{'✅' if ok else '⛔':<6}{w}")
        print(f"\n放行 {len(passed)} / {len(cands)} 篇")
        return 0

    if not passed:
        blocked = [f"{n}：{w}" for n, ok, w in cands if not ok][-3:]
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 无稿通过闸门，不发布。最近 3 篇原因：")
        for b in blocked:
            print("  -", b)
        return 0

    done_today = published_today()
    if args.only:
        # 手动指定：绕过每日配额（是人在决定发几篇），但闸门照走 ——
        # passed 已经是过闸门的列表，指定了没过闸门的篇目会在这里被剔掉并明确报出来。
        want = list(dict.fromkeys(args.only))
        by_name = {n: w for n, w in passed}
        batch = [(n, by_name[n]) for n in want if n in by_name]
        for n in want:
            if n not in by_name:
                why = next((w for nm, ok, w in cands if nm == n), "不在成稿列表里")
                print(f"⛔ 跳过 {n}：{why}")
        if not batch:
            print("指定的稿都没过闸门，不发布。")
            return 1
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 手动指定 {len(batch)} 篇"
              f"（今日已发 {done_today}，本次不受配额 {DAILY_QUOTA} 限制）\n")
    else:
        quota = DAILY_QUOTA - done_today
        if quota <= 0 and not args.dry_run:
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] 今日已发 {done_today} 篇，达到每日配额 {DAILY_QUOTA}，不再发布。")
            return 0
        batch = passed[:max(quota, 1) if not args.dry_run else 1]
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 闸门通过 {len(passed)} 篇，"
              f"今日已发 {done_today}/{DAILY_QUOTA}，本次处理 {len(batch)} 篇\n")
    # ⛔ 一次只预填一篇。创作平台的发布页一次只承载一篇稿，连续预填第二篇会把第一篇
    # 直接覆盖掉 —— 而预填后最后一步（选时段、点发布）要人来点，人还没点，稿就没了。
    # 2026-08-03 实测：连预填 3 篇，页面上只剩最后一篇，前两篇白填。
    # DAILY_QUOTA=3 的含义是「一天发 3 篇」，靠一天跑三次实现，不是一次填三篇。
    # 所以改配额必须同步改 plist 的 StartCalendarInterval 触发点数量，否则配额是空头支票。
    rest = batch[1:]
    name, why = batch[0]
    print(f"--- {name}\n    {why}")
    rc = publish_one(name, args.dry_run, args.now)
    if rest:
        print(f"\n还有 {len(rest)} 篇在队列里，**发完这篇再跑一次**才会预填下一篇"
              f"（创作平台一次只放得下一篇）：")
        for n, _ in rest:
            print(f"  · {n}")
        print(f"  发完后先回填：python3 auto_publish.py --confirm {name} --at \"YYYY-MM-DD HH:MM\"")
    return rc


if __name__ == "__main__":
    sys.exit(main())
