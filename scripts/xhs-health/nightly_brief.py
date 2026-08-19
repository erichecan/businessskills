#!/usr/bin/env python3
"""每晚 21:00 生成当日 brief —— 把「今天到底跑了没有」写成一页纸。

覆盖 5 项（IP 形象切图不在内，那步在下载目录里、靠人肉眼确认哪一批是新的）：
  1. 采集：今天跑了几轮、记忆库涨了多少
  2. 词：今天投放几个词、新发现几个
  3. 成稿：今天出了几篇、独立审核判了什么
  4. 可发布：闸门放行几篇，并与发布数据.csv 交叉核对，剔掉「日志漏记但其实已发」的
  5. 定时任务健康：五个 launchd 任务的最近退出码

第 5 项是这份 brief 存在的真正理由。2026-08-05 查出五个定时任务全部因
「launchd 读不了 /Volumes 外置卷」静默失败（EPERM），日志只写进 /tmp 没人看，
采集/审核/发布全靠人在会话里手动补。没有这一项，brief 本身也会安静地死掉而没人知道。

用法：
  python3 nightly_brief.py            # 生成 docs/YYYYMMDD-brief.md 并打印
  python3 nightly_brief.py --stdout   # 只打印不写文件
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
DOCS = REPO / "docs"
PREVIEW = REPO / "preview"
RUN_LOG = SUCAI / "运行日志.csv"
KW_POOL = SUCAI / "关键词池.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
PUB_DATA = SUCAI / "发布数据.csv"

LAUNCHD_JOBS = {
    # 常驻服务，不是定时任务，但它一挂后面全挂 —— 采集/发布/数据回收都要经过它
    "com.eric.cdpproxy": "CDP 代理（常驻）",
    "com.eric.xhscollect": "采集（供给源头）",   # 2026-08-14 加
    "com.eric.xhsprobe": "采集探测",
    "com.eric.xhsdata": "发布数据回收",
    "com.eric.xhsaudit": "独立审核",
    "com.eric.xhshealth": "健康检查",
    "com.eric.xhspublish": "全自动发布",
    "com.eric.xhswrite": "写稿 loop",
    "com.eric.xhstriage": "待人工分诊",   # 2026-08-13 加，不列进来它挂了没人知道
    # 把自己也列进来：这份 brief 本身也是 launchd 任务，它静默失败时不会有人发现，
    # 只能靠下一次成功运行（或人肉跑）时看见「上次退出码 2」才知道中间断过。
    "com.eric.xhsbrief": "每日 brief（本任务）",
}


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def section_collect(today):
    rows = [r for r in read_csv(RUN_LOG) if (r.get("日期") or "").strip() == today]
    if not rows:
        return ["❌ **采集**：今天没有任何运行记录（预期每天 4 轮 run1–run4）"], False
    def num(r, col):
        v = (r.get(col) or "").strip()
        return int(v) if v.isdigit() else 0
    # 各轮「本轮新增」之和 ≠ 记忆库累计的日增量（跨轮去重），两个数都给，不合成一个假数字
    added = sum(num(r, "本轮新增条数") for r in rows)
    runs = "、".join((r.get("轮次") or "?") for r in rows)
    ok = len(rows) >= 4
    mark = "✅" if ok else "⚠️"
    return [f"{mark} **采集**：{len(rows)} 轮（{runs}）· 记忆库累计 {num(rows[-1], '记忆库累计')} "
            f"· 各轮新增合计 {added} 条"], ok


def section_keywords(today):
    rows = read_csv(KW_POOL)
    new = [r for r in rows if (r.get("首次发现") or "").strip() == today]
    active = [r for r in new if (r.get("类型") or "").strip() == "活跃"]
    # 今日投放词数写在运行日志第 3 列（每轮 8 词），比反查关键词池可靠
    run_rows = [r for r in read_csv(RUN_LOG) if (r.get("日期") or "").strip() == today]
    probed = 0
    for r in run_rows:
        v = list(r.values())[2] if len(r.values()) > 2 else ""
        if (v or "").strip().isdigit():
            probed += int(v)
    lines = [f"📈 **词**：投放 {probed} 词次 · 新发现 {len(new)} 个"
             f"（其中首投即升活跃 {len(active)} 个）· 词池累计 {len(rows)}"]
    if active:
        lines.append("　　新升活跃：" + "、".join((r.get("关键词") or "") for r in active))
    return lines, len(new) > 0


def section_drafts(today):
    files = sorted(SUCAI.glob(f"成稿_{today}_*.md")) + \
            sorted((SUCAI / "归档稿").glob(f"成稿_{today}_*.md"))
    if not files:
        return ["❌ **成稿**：今天 0 篇"], False
    # 一篇稿可能被审多轮，只看最后一条独立审核
    audits = {}
    for r in read_csv(AUDIT_LOG):
        if (r.get("审核方") or "").strip() == "独立审核":
            audits[(r.get("成稿文件") or "").strip()] = r
    lines = [f"📝 **成稿**：{len(files)} 篇"]
    for f in files:
        a = audits.get(f.name)
        stem = f.name.removeprefix("成稿_").removesuffix(".md")
        imgs = len(list((SUCAI / "成品图" / stem).glob("*.png"))) \
            if (SUCAI / "成品图" / stem).is_dir() else 0
        where = "归档稿/" if f.parent.name == "归档稿" else ""
        if a:
            lines.append(f"　　· {where}{f.name} — {a.get('总分')}分 {a.get('评级')} · "
                         f"处置「{a.get('处置')}」· 成品图 {imgs} 张")
        else:
            lines.append(f"　　· {where}{f.name} — 无独立审核 · 成品图 {imgs} 张")
    return lines, True


def published_titles():
    """发布数据.csv 是从创作后台抓回来的真实已发列表，比发布日志可靠——
    发布日志只记本脚本走过的流程，人工在页面上直接发的不会进去。"""
    return {(r.get("标题") or "").strip() for r in read_csv(PUB_DATA) if (r.get("标题") or "").strip()}


def draft_title(path):
    """必须走 case_entry.parse_draft —— 成稿的 H1 写的是关键词，真正发出去的标题在
    「## 发布标题」段。用 H1 会把「同一关键词的第二篇稿」误判成重复发布。"""
    if not path.exists():
        return ""
    sys.path.insert(0, str(REPO / "scripts" / "case-entry"))
    try:
        from case_entry import parse_draft
        return (parse_draft(path.read_text(encoding="utf-8")).get("title") or "").strip()
    except Exception:
        return ""


def section_coverage(today):
    """七种力 × 场景 的产出覆盖 —— S 组唯一的验收信号。

    这一节回答的问题是「选题面到底有没有打开」。在它之前，偏斜是**看不见**的：
    140 篇成稿里结构力 105 篇、示弱力 2 篇、37 个场景 15 个零产出，
    而每天的 brief 只报「今天出了几篇」，报不出「一直在同一个格子里出」。

    ⛔ 两周后这里还是结构力独大，说明 S3 的配额没生效，回去重新设计，
    别改这一节的阈值把警报调没了。
    """
    sys.path.insert(0, str(REPO / "scripts"))
    import scene_map
    sys.path.insert(0, str(REPO / "scripts" / "xhs-loop"))
    from refine_loop import QUOTA_DAYS, scene_output

    terms = scene_map.load_terms()
    scenes = scene_map.load_scenes()
    m = scene_map.matrix()
    recent = scene_output(QUOTA_DAYS)

    # 近 N 天的概念分布要按场景反算 —— matrix() 是全历史的
    tag_of = {r["场景"]: r["默认概念"] for r in scenes}
    rc = {}
    for sc, n in recent.items():
        for c in tag_of.get(sc, "").split("/"):
            if c:
                rc[c] = rc.get(c, 0) + n

    zero_recent = [t for t in terms if not rc.get(t)]
    zero_scene = [r["场景"] for r in scenes if r["场景"] not in m["场景"]]
    lines = [f"🎯 **选题覆盖**（近 {QUOTA_DAYS} 天 {sum(recent.values())} 篇 · 累计 {m['已打标']} 篇）"]
    lines.append("　　七种力：" + " · ".join(
        f"{t} {rc.get(t, 0)}/{m['概念'].get(t, 0)}" for t in terms) + "（近{}天/累计）".format(QUOTA_DAYS))
    if zero_recent:
        lines.append(f"　　⚠️ 近 {QUOTA_DAYS} 天**零产出**的概念 {len(zero_recent)} 个："
                     + "、".join(zero_recent))
    lines.append(f"　　零产出场景 {len(zero_scene)}/{len(scenes)}"
                 + ("：" + "、".join(zero_scene[:8]) + ("…" if len(zero_scene) > 8 else "")
                    if zero_scene else ""))

    gaps = [r for r in read_csv(SUCAI / "缺词信号.csv")
            if (r.get("日期") or "").strip() == today]
    if gaps:
        lines.append(f"　　今天记下 {len(gaps)} 条缺词信号（该做但词库里没词）："
                     + "、".join(f"{r.get('场景')}" for r in gaps[:6]))
        lines.append("　　　这些会在下一轮 daily_collect 里作为定向种子词投出去（占 2 个种子名额）")
    # 告警判据两条，任一触发就进「今天需要你处理」：
    #   · 近期零产出的概念 > 2 个
    #   · 零产出场景**过半** —— 比例而不是绝对数，因为场景表还会继续加行
    # ⛔ 别把阈值调松来消警报。这一节的存在意义就是让「一直在同一个格子里出稿」
    #   这件事没法被忽略，调松等于把 S 组的验收信号关掉。
    return lines, len(zero_recent) <= 2 and len(zero_scene) <= len(scenes) // 2


def section_comments(today):
    """评论三战场：首评 / 读者回复 / 外部评论。

    ⛔ 这一节最该显眼的不是「今天发了几条」，是**存活率和熔断** ——
    评论被折叠或删除是最早的风控信号，比封号早。花钱看板看不出这个。
    """
    sys.path.insert(0, str(REPO / "scripts" / "xhs-comment"))
    try:
        import outreach as O
    except Exception as e:                                  # noqa: BLE001
        return [f"💬 **评论**：读不出台账（{e}）"], True
    from collections import Counter
    from datetime import datetime

    ledger = O.read_ledger()
    if not ledger:
        return ["💬 **评论**：台账还是空的（C0 未完成：www 主站没登录态，发送链路没通）"], True

    now = datetime.now()
    st = Counter(r.get("状态", "") for r in ledger)
    sent_today = [r for r in ledger if r.get("状态") == "已发送"
                  and (r.get("时间") or "").startswith(today)]
    alive = sum(1 for r in ledger if r.get("存活校验") == "在")
    dead = sum(1 for r in ledger if r.get("存活校验") == "没了")
    on, why = O.breaker_on(now)

    lines = [f"💬 **评论**：台账 {len(ledger)} 条 · "
             + " · ".join(f"{k} {v}" for k, v in st.most_common())]
    lines.append(f"　　今日已发 {len(sent_today)}/{O.DAILY_CAP} 条")
    if alive or dead:
        rate = alive / (alive + dead)
        flag = "  ⛔ 低于下限，该熔断了" if rate < O.ALIVE_FLOOR else ""
        lines.append(f"　　存活率 **{rate:.0%}**（在 {alive} · 没了 {dead}）{flag}")
    else:
        lines.append("　　存活率：还没有已校验的样本")
    if on:
        lines.append(f"　　⛔⛔ **熔断中**：{why} —— 不要手动绕过，先查为什么被折叠")
    by_concept = Counter(c for r in ledger for c in (r.get("概念") or "").split("/") if c)
    if by_concept:
        lines.append("　　打在哪些概念的场景上：" + " · ".join(
            f"{k} {v}" for k, v in by_concept.most_common(5)))
    ok = not on and (not (alive or dead) or alive / (alive + dead) >= O.ALIVE_FLOOR)
    return lines, ok


def section_publish():
    sys.path.insert(0, str(REPO / "scripts" / "xhs-publish"))
    try:
        from auto_publish import candidates
    except Exception as e:
        return [f"⚠️ **可发布**：闸门评估失败（{e}）"], False
    cands = candidates()
    passed = [(n, w) for n, ok, w in cands if ok]
    already = published_titles()
    real, stale = [], []
    for n, w in passed:
        t = draft_title(SUCAI / n) or draft_title(SUCAI / "归档稿" / n)
        (stale if t and t in already else real).append((n, w, t))
    lines = [f"🚀 **可发布**：闸门放行 {len(passed)}/{len(cands)} 篇，"
             f"扣除后台已存在的 {len(stale)} 篇 → **真正可发 {len(real)} 篇**"]
    for n, w, _ in real:
        lines.append(f"　　· {n} — {w}")
    if stale:
        lines.append(f"　　⚠️ 闸门放行但后台已有同名笔记（发布日志漏记 ✅ 行，再发即重复）：")
        for n, _, t in stale:
            lines.append(f"　　　 {n}（后台标题「{t}」）")
    lines.append("　　注：创作平台一次只放得下一篇，每篇预填后最后一步（选时段+点发布）仍需人点约 10 秒。")

    # 存量债：审核判过「发布」、却被**后来收紧的机械规则**挡在外面的稿。
    # 2026-08-15 加。这批稿掉在没人管的夹缝里 —— rework_queue 只取处置=返工，
    # 它们是「发布」；闸门又因机械项拦下。08-08 换 CTA 口径那次一口气废掉 17 篇，
    # 攒到 55 篇曾达标却没发出去才被发现。让它每天自己冒出来，别再靠人想起来查。
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "draft_check.py"),
                            "--regress"], capture_output=True, text=True, timeout=300)
        if r.returncode == 1:
            head = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
            n = re.search(r"(\d+) 篇", head)
            names = [l.strip() for l in r.stdout.splitlines()
                     if l.startswith("  成稿_")]
            lines.append(f"　　⚠️ 另有 **{n.group(1) if n else '?'} 篇**审核判「发布」但卡在当前机械规则上"
                         f"（规则收紧留下的存量债，跑 `draft_check.py --regress` 看明细）：")
            for nm in names[:6]:
                lines.append(f"　　　 {nm}")
    except Exception as e:
        lines.append(f"　　⚠️ 存量回归检查失败：{e}")

    return lines, len(real) > 0


def section_calibrate():
    """审核标准的校准进度 —— 唯一会「反向改标准」的回路，别让它静默停着。

    这一节存在的理由和第 5 项一样：没有它，「等数据够了再校准」会变成永远不校准，
    因为没有任何地方会提醒你还差几篇。
    """
    sys.path.insert(0, str(REPO / "scripts" / "xhs-health"))
    try:
        from calibrate_audit import MIN_SAMPLE, build_pairs
        paired, pending = build_pairs(7)
    except Exception as e:
        return [f"⚠️ **审核校准**：算不出来（{e}）"], False
    n, need = len(paired), MIN_SAMPLE
    if n >= need:
        return [f"🔬 **审核校准**：样本 {n}/{need} 篇**已够**，"
                f"跑 `python3 scripts/xhs-health/calibrate_audit.py` 出报告"], True
    lines = [f"🔬 **审核校准**：样本 {n}/{need} 篇，还差 {need - n} 篇才够反推审核标准"]
    for r in sorted(pending, key=lambda x: -x["天数"])[:3]:
        lines.append(f"　　· {r['发布标题'][:24]} — 已发 {int(r['天数'])} 天，还差 {7 - int(r['天数'])} 天")
    # 不算失败：样本在攒是正常状态，标红只会让人对红色脱敏
    return lines, True


def section_outcome():
    """数据回填与预测复盘跑完之后 —— **该做什么决定**。

    ⛔ 2026-08-14 加。Eric 问「数据回填后、预测复盘归因后，有什么实际的行动？」
    查下来两条链路都断在「打印出来」这一步，实际行动是零：

      ① 复盘 → predict：结论写进 预测校准.csv，predict.py 预测时把偏差打印到
         refine_loop 的日志里 —— 而那个日志没人看。复盘自己又立了「≥5 篇才准改系数」
         的规矩，样本永远差几篇，于是永远停在提示。
      ② 搜索来源占比 → **完全没有下游**。健康检查只管催「满 7 天还没回填」，
         回填之后没有任何脚本读它 —— 而它是 L3 唯一主指标，
         整条搜索流策略成不成立就在这几个数字里。

    所以这一节不报「跑了没有」，只报**数字本身和它逼出来的那个决定**。
    数据的用处是改变行为；只要没有任何行为因它而变，采集和复盘就是在空转。
    """
    lines, ok = ["📊 **数据说了什么**"], True

    # ── 搜索来源占比：账号策略的成败就在这一列
    rows = read_csv(SUCAI / "词库.csv")
    pub = [r for r in rows if (r.get("发布日") or "").strip()]
    got = [r for r in pub if (r.get("搜索来源占比") or "").strip()]
    if not got:
        lines.append(f"　　· 搜索来源占比：{len(pub)} 篇已发布，**一篇都还没回填** —— 主指标全空")
    else:
        vals = []
        for r in got:
            try:
                vals.append(float((r.get("搜索来源占比") or "").strip().rstrip("%")))
            except ValueError:
                pass
        avg = sum(vals) / len(vals) if vals else 0
        lines.append(f"　　· 搜索来源占比：{len(got)}/{len(pub)} 篇有数 · 均值 **{avg:.1f}%**"
                     f"（{'/'.join(f'{v:g}%' for v in sorted(vals, reverse=True)[:5])}）")
        # 判据写死在这里而不是让人自己看：搜索流的全部理由就是「靠搜索被找到」。
        # 占比常年个位数，说明流量不是搜来的，那么按搜索位强度选词这套打法就该重估。
        if vals and avg < 10:
            lines.append(f"　　  ⚠️ 均值 {avg:.1f}% —— 流量基本不是搜来的。"
                         f"搜索流这套打法（按搜索位强度选词、标题塞关键词）"
                         f"要不要继续，该拿这组数字重估一次了")

    # ── 话题集中度：重复发同一个词是在跟自己抢搜索位
    # 2026-08-15 Eric 提出「已经有差不多 6 篇汇报被打断了」，查证属实：
    # 后台 33 篇里「被打断」类 5 篇、「绩效面谈被打低分」3 篇。
    # 而数据显示重复发不划算 —— 晋升答辩 1050→301、绩效 157→107→107、
    # 汇报被打断 62→61→60，量不累积反而集体停在低位。
    # 闸门已加同词上限，这里让分布**在发生之前**就看得见。
    # 2026-08-15 起配额分两层（同词同角度 ≤2 / 同词总量 ≤4），这里跟着按两层报，
    # 并显式点出「未声明角度」有多少 —— 角度没声明的稿全挤在一个桶里，
    # 是当前最容易把配额吃光的原因，得让它看得见。
    try:
        sys.path.insert(0, str(REPO / "scripts" / "xhs-publish"))
        from auto_publish import (ANGLE_UNSET, MAX_PER_KEYWORD_ANGLE,
                                  MAX_PER_KEYWORD_TOTAL, published_angle_counts,
                                  published_keyword_counts)
        counts = published_keyword_counts()
        angles = published_angle_counts()
        hot = [(k, v) for k, v in counts.items() if v >= 2]
        if hot:
            hot.sort(key=lambda x: -x[1])
            desc = "、".join(f"{k}×{v}" for k, v in hot[:4])
            lines.append(f"　　· 话题集中度：{len(counts)} 个词已发布 · 重复词 {len(hot)} 个（{desc}）")
            full_angle = [(k, a) for (k, a), v in angles.items() if v >= MAX_PER_KEYWORD_ANGLE]
            full_total = [k for k, v in counts.items() if v >= MAX_PER_KEYWORD_TOTAL]
            if full_angle:
                lines.append(f"　　  ⚠️ {len(full_angle)} 个「词×角度」已达上限 "
                             f"{MAX_PER_KEYWORD_ANGLE} 篇 —— 同角度会被拦，"
                             f"换个角度（写 `> 角度：xxx`）或换词")
            if full_total:
                lines.append(f"　　  ⚠️ {len(full_total)} 个词各角度合计已达 "
                             f"{MAX_PER_KEYWORD_TOTAL} 篇上限 —— 角度再多也该换词了")
        unset = sum(v for (_, a), v in angles.items() if a == ANGLE_UNSET)
        if unset:
            lines.append(f"　　  · 已发布里 {unset} 篇未声明角度，共用一个配额桶"
                         f"（新稿在成稿头部写 `> 角度：xxx` 才另开配额）")
    except Exception:                                       # noqa: BLE001
        pass

    # ── 预测校准：样本够没够，够了就该动手
    calib = read_csv(SUCAI / "预测校准.csv")
    views = [r for r in calib if (r.get("指标") or "").strip() == "观看"]
    if views:
        by_density = {}
        for r in views:
            d = (r.get("密度") or "?").strip()
            try:
                m = float(r.get("倍数") or 0)
            except ValueError:
                continue
            if m > 0:
                by_density.setdefault(d, {})[(r.get("关键词") or "")] = m
        for d, kws in sorted(by_density.items()):
            ms = sorted(kws.values())
            mid = ms[len(ms) // 2]
            enough = len(ms) >= 5
            lines.append(
                f"　　· 预测校准「{d}密度」：{len(ms)} 篇独立样本 · 实际/预测中位 **{mid:.2f}×**"
                + ("　✅ 样本已够，可以改 VIEWS_BASE 了" if enough
                   else f"（差 {5 - len(ms)} 篇够 5）"))
            # 量级级别的错误不必等样本 —— 复盘自己写过这个口子。
            if mid < 0.5 and not enough:
                lines.append(f"　　  ⚠️ 高估 {1 / mid:.1f} 倍，已是量级错误。"
                             f"复盘定过「累计 5 篇前只调明显错的量级」，这一档符合那个口子")
    else:
        lines.append("　　· 预测校准：还没有观看指标的对账样本")

    return lines, ok


def section_launchd():
    lines, healthy = [], True
    for label, desc in LAUNCHD_JOBS.items():
        out = subprocess.run(["launchctl", "list", label], capture_output=True, text=True).stdout
        m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', out)
        if not out.strip():
            lines.append(f"　　❌ {desc}（{label}）未加载")
            healthy = False
            continue
        code = int(m.group(1)) if m else -1
        # launchctl 报的是 wait status：真实退出码要右移 8 位
        rc = code >> 8 if code > 255 else code
        if rc == 0:
            lines.append(f"　　✅ {desc} — 上次退出 0")
        elif rc == 3:
            # ⛔ 2026-08-13：退出码 3 = 「任务跑成功了，但它发现了问题」
            # （health_check.EXIT_ALERTS）。此前 health_check 有告警就退 1，
            # 这里一律画成 ❌ 静默失败 —— 监控自己天天挂红，人很快就对红色脱敏，
            # 于是真正挂掉的任务（比如当天全挂的 probe）反而混在里面看不出来。
            # 任务健康与内容健康是两件事，这一栏只该报前者。
            lines.append(f"　　⚠️ {desc} — 跑通了，但报出告警（见上面各节 / 健康告警.md）")
        else:
            hint = "（外置卷权限：launchd 读不了 /Volumes）" if rc in (2, 126) else ""
            lines.append(f"　　❌ {desc} — 上次退出码 {rc}{hint}")
            healthy = False
    head = "🩺 **定时任务**：" + ("全部正常" if healthy else "**有任务在静默失败**")
    return [head] + lines, healthy


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1a1d21;--dim:#6b7280;--line:#e5e7eb;
 --ok:#0f9d58;--warn:#d97706;--bad:#dc2626;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--card:#1c1f24;--fg:#e8eaed;
 --dim:#9aa0a6;--line:#2c3036;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--accent:#60a5fa}}
*{box-sizing:border-box}
body{margin:0;padding:28px 18px 64px;background:var(--bg);color:var(--fg);
 font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px 18px;margin-bottom:14px;border-left:4px solid var(--ok)}
.card.bad{border-left-color:var(--bad)}
.card h2{font-size:15px;margin:0 0 10px;font-weight:600;display:flex;
 align-items:center;gap:8px;flex-wrap:wrap}
.pill{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;
 background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.card.bad .pill{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
ul{margin:0;padding-left:0;list-style:none}
li{padding:5px 0 5px 14px;border-left:2px solid var(--line);margin-left:2px;
 color:var(--dim);font-size:13.5px}
li.warn{border-left-color:var(--warn);color:var(--fg)}
li.err{border-left-color:var(--bad);color:var(--fg)}
b{font-weight:600;color:var(--fg)}
.todo{background:color-mix(in srgb,var(--bad) 8%,var(--card));
 border:1px solid color-mix(in srgb,var(--bad) 30%,var(--line));border-left-width:4px;
 border-left-color:var(--bad)}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;
 background:color-mix(in srgb,var(--fg) 7%,transparent);padding:1px 5px;border-radius:4px}
.foot{color:var(--dim);font-size:12px;margin-top:26px;text-align:center}
"""


def _disabled_labels():
    """launchctl 的 disabled 名单。

    ⛔ 这是 `launchctl list` 看不出来的一种失败：被 `launchctl disable` 的任务
    **不在 list 里，也不会自己过期**，而 bootstrap 会「成功」却永远不触发。
    8/16 九个任务停摆两天、ximalaya.daily 建立后一次都没自动跑过（runs=0），
    都是这一种 —— 两次都不是「不知道怎么恢复」，是**没有任何信号说它还没被恢复**。
    """
    out = subprocess.run(["launchctl", "print-disabled", f"gui/{os.getuid()}"],
                         capture_output=True, text=True).stdout
    return {m.group(1) for m in re.finditer(r'"([^"]+)"\s*=>\s*disabled', out)}


# 跨项目心跳：只读别人的日志，不碰别人的调度。
# 边界说明（2026-08-18）：ximalaya 的 pause/resume 归它自己管（见 xhs-schedule 注释），
# 但**观测**留在这里 —— Eric 只看这一份 brief，告警分散到第二个地方等于没有。
# 控制分离、观测集中。读不到就静默跳过，绝不因为隔壁项目的路径变了而拖垮本 brief。
FOREIGN_HEARTBEATS = {
    "喜马拉雅 daily": (Path.home() / "Library/Logs/ximalaya/daily.log", "com.eric.ximalaya.daily", 24),
}


# 跨仓契约（ximalaya 侧 commit 8860f9f 定义，要改会先知会本项目）：
#   0 = 正常 / 未来 1 天档满 / --publish 0 / **部分成功**
#       （档满和部分成功返 0 都是**故意**的：每天恰好 2 集的保证不是故障，
#        发 2 集成了 1 集是降级不是失败 —— 为它们报警会让补射每次生效都触发假警报）
#   2 = 库存空，一集都没排上期      → 今天没发出去，但补库存那条线能自愈
#   3 = 有货要发，但一集都没发成    → 发布链路本身坏了，**不会自愈**
#
# 这两个码以前都是静默的：跑得完完整整、退出码 0、日志漂漂亮亮，就是没发东西，
# 在外面和正常轮次**完全同形**。日志里那句「⛔ 连续两集没发成」写得又清楚又醒目，
# 但这条线是无人值守的 —— 写给人看的部分是给**排查**用的，不是给**发现**用的。
FOREIGN_EXIT_CODES = {
    2: "那轮**库存空、一集没排上**（退出码 2）—— 「发布必保」今天没做到，去看补库存那条线",
    # 3 比 2 急：2 有补库存兜着、第二天能恢复；3 是持续性的，三次补射会被一起用光，
    # 人不介入就会一直发不出去。
    3: "那轮**一集都没发成**（退出码 3）—— ⚠️ 多半是登录态掉了或页面改版，"
       "**补射也救不回来、不会自愈**，去 daily.log 看最后一集的报错",
}


def _last_run(log: Path):
    """读日志里**最后一对** ▶/◀，返回 (启动时间, 退出码)。退出码 None = 有 ▶ 没 ◀。

    ⛔ 只看 `▶` 是不够的：它只证明 runner 被拉起来了，不证明这轮干成了事。
    对「发布必保」这条线，典型的失败形态恰恰是 Chrome 没起 / 登录态掉了 / 页面改版 ——
    runner 照样被拉起、照样写 ▶，然后非 0 退出。只看 ▶ 会判成健康。
    中途被杀更隐蔽：有 ▶、根本没有 ◀。

    ⚠️ **必须只看最后一次，不能因为当天出现过非 0 就报警。** 这条线 21:00 之外还有
    22:30 / 23:45 两次补射，「21:00 失败、22:30 成功」是**正常且预期**的形态。
    按「当天有过失败」报警的话，每次补射生效都会收到一条假警报 —— 而假警报多了
    这个 section 就会被跳过，那就绕回「没人看」，白做。

    日志契约（跨仓，ximalaya 侧要改会先知会，见对方 docs/20260818-定时任务恢复核查.md）：
        ▶ 2026-08-18 16:33:56 EDT  daily --no-make
        ◀ 退出码 0 · 16:33:56
    """
    t, code = None, None
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("▶"):
            f = line.split()
            t, code = datetime.strptime(f"{f[1]} {f[2]}", "%Y-%m-%d %H:%M:%S"), None
        elif line.startswith("◀"):
            f = line.split()
            code = int(f[2]) if len(f) > 2 and f[2].lstrip("-").isdigit() else -1
    return t, code


def section_heartbeat():
    """定时任务「本该跑却没跑」的检查 —— 花钱看板看不出这个。"""
    lines, ok = ["## 定时任务心跳"], True
    disabled = _disabled_labels()

    bad = sorted(disabled & set(LAUNCHD_JOBS))
    if bad:
        ok = False
        for j in bad:
            lines.append(f"　　⛔ {j} 被 launchctl disable —— bootstrap 会「成功」但永不触发，"
                         f"需 `launchctl enable gui/{os.getuid()}/{j}`")
    else:
        lines.append("　　✅ 本项目任务无一处于 disabled")

    for name, (log, label, max_h) in FOREIGN_HEARTBEATS.items():
        if label in disabled:
            lines.append(f"　　⛔ {name}（{label}）被 disable，不会触发")
            ok = False
            continue
        try:
            last, code = _last_run(log)
            assert last is not None
        except (OSError, IndexError, ValueError, AssertionError) as e:
            lines.append(f"　　· {name}：读不到运行记录（{type(e).__name__}），跳过")
            continue
        hours = (datetime.now() - last).total_seconds() / 3600
        when = f"{last:%m-%d %H:%M}"
        if hours > max_h:
            lines.append(f"　　⛔ {name} 已 {hours:.0f} 小时没运行（上次 {when}，阈值 {max_h}h）"
                         f"—— 排期是排未来的，漏掉的档追不回来")
            ok = False
        elif code is None:
            lines.append(f"　　⛔ {name} {when} 那轮**没跑完就死了**（有 ▶ 无 ◀）")
            ok = False
        elif code != 0:
            lines.append(f"　　⛔ {name} {when} " + FOREIGN_EXIT_CODES.get(
                code, f"那轮**失败**（退出码 {code}）—— 典型原因是 Chrome 没起 / 登录态掉了 / 页面改版"))
            ok = False
        else:
            lines.append(f"　　✅ {name} {hours:.0f} 小时前跑过并正常退出（{when}）")
    return (lines, ok)


def section_usage():
    """额度消耗看板。见 usage_report.py —— 口径和「为什么必须按 message.id 去重」都在那。"""
    try:
        import usage_report
        text = usage_report.report()
        over = usage_report.WARN_AT
    except Exception as e:                                    # noqa: BLE001
        return ([f"## 额度消耗", f"　　⚠️ 统计失败：{e}"], True)   # 统计挂了不该拦住 brief
    lines = [l if l.startswith("##") else "　" + l for l in text.splitlines() if l.strip()]
    total = 0.0
    for l in text.splitlines():
        if l.strip().startswith("合计"):
            try:
                total = float(l.split("$")[1].split()[0])
            except (IndexError, ValueError):
                pass
    return (lines, total < over)


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(s):
    """把 brief 行里的 **粗体** 和 `代码` 转成标签。转义在前，避免正文里的 < 破坏结构。"""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def render_html(today, blocks):
    """和 md 版共用同一批 blocks —— 两个格式各渲染一遍同一份数据，
    不是各算各的。否则哪天改了统计口径，两份 brief 会悄悄给出不同的数字。"""
    cards = []
    for lines, ok in blocks:
        head, *rest = lines
        # 首行形如「✅ **采集**：4 轮…」：拆出 emoji、标题、结论三段
        m = re.match(r"^(\S+)\s+\*\*(.+?)\*\*[：:]\s*(.*)$", head)
        icon, name, summary = m.groups() if m else ("•", head, "")
        items = []
        for l in rest:
            t = l.strip().lstrip("　").strip()
            if not t:
                continue
            cls = "err" if t.startswith("❌") else ("warn" if t.startswith(("⚠️", "·")) else "")
            items.append(f'<li class="{cls}">{_inline(t)}</li>')
        cards.append(
            f'<div class="card{"" if ok else " bad"}">'
            f'<h2><span>{icon}</span>{_esc(name)}'
            f'<span class="pill">{"正常" if ok else "需处理"}</span></h2>'
            f'<div>{_inline(summary)}</div>'
            + (f'<ul>{"".join(items)}</ul>' if items else "")
            + "</div>")

    fails = [lines[0] for lines, ok in blocks if not ok]
    todo = ""
    if fails:
        rows = "".join(f'<li class="err">{_inline(f)}</li>' for f in fails)
        todo = f'<div class="card todo"><h2><span>⚠️</span>今天需要你处理</h2><ul>{rows}</ul></div>'

    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>每日 brief · {today}</title><style>{CSS}</style></head><body><div class="wrap">'
            f'<h1>小红书 · 每日 brief</h1>'
            f'<div class="sub">{today} · 由 <code>nightly_brief.py</code> 每晚 21:00 生成</div>'
            f'{todo}{"".join(cards)}'
            f'<div class="foot">数据源：运行日志 · 关键词池 · 审核记录 · 发布数据 · launchctl</div>'
            f'</div></body></html>')


def notify_brief(today, blocks, path):
    """brief 生成完弹一条系统通知（2026-08-14 Eric 要的）。

    为什么需要：brief 以前生成完就静静躺在 docs/ 里，全靠人记得每天 21 点后去开。
    这个项目里已经反复验证过——只要依赖人主动去看，实际执行率就趋近于零
    （「待人工」积压 12 天、复盘结论从没被执行过，都是这个模式）。

    通知里放**当天最要紧的那条**，不是「brief 已生成」这种无信息量的话：
    看一眼通知就知道今天要不要动手，不用先打开文件才发现全绿。

    点通知直接开页面走 terminal-notifier 的 -open（2026-08-14 Eric 让装的）：
    osascript 的 display notification 不支持附加点击动作，点了只会消失。
    没装或调用失败时退回 osascript —— 通知本身比「能不能点」重要，
    不能因为少个工具就整条提醒都没了。
    """
    fails = [lines[0] for lines, ok in blocks if not ok]
    if fails:
        first = re.sub(r"[*#`]", "", fails[0])[:90]
        text = f"{len(fails)} 项要处理：{first}"
    else:
        text = "全绿，没有需要你处理的"
    title = f"小红书 brief · {today[5:]}"
    tn = shutil.which("terminal-notifier") or "/opt/homebrew/bin/terminal-notifier"
    if Path(tn).exists():
        try:
            subprocess.run([tn, "-title", title, "-message", text,
                            "-subtitle", "点这条打开今天的 brief",
                            "-open", path.as_uri(),
                            "-sound", "Basso" if fails else "Glass",
                            # 固定 group：同一天多次生成时替换掉上一条，不堆一串
                            "-group", "xhs-brief"],
                           check=False, timeout=10)
            return
        except Exception:                                   # noqa: BLE001
            pass
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "{title}"'
             f' subtitle "preview/brief-latest.html"'
             f' sound name "{"Basso" if fails else "Glass"}"'],
            check=False, timeout=10)
    except Exception:                                       # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="只打印不写文件")
    args = ap.parse_args()

    today = date.today().isoformat()
    blocks = [section_collect(today), section_keywords(today), section_drafts(today),
              section_coverage(today), section_comments(today),
              section_publish(), section_outcome(), section_calibrate(),
              section_launchd(), section_heartbeat(), section_usage()]
    body = [f"# 每日 brief · {today}", ""]
    for lines, _ in blocks:
        body += lines + [""]
    fails = [l for lines, ok in blocks if not ok for l in lines[:1]]
    if fails:
        body += ["## ⚠️ 今天需要你处理", ""] + [f"- {l}" for l in fails]
    text = "\n".join(body).rstrip() + "\n"

    print(text)
    if not args.stdout:
        DOCS.mkdir(exist_ok=True)
        md_out = DOCS / f"{today.replace('-', '')}-brief.md"
        md_out.write_text(text, encoding="utf-8")
        print(f"→ 已写入 {md_out}")
        PREVIEW.mkdir(exist_ok=True)
        html = render_html(today, blocks)
        html_out = PREVIEW / f"{today.replace('-', '')}-brief.html"
        html_out.write_text(html, encoding="utf-8")
        print(f"→ 已写入 {html_out}")
        # 固定路径（2026-08-14 Eric 要的）：按日期命名的那份是存档，
        # 这份始终是最新一天，路径不变才能钉在 Dock / 存成书签，一次点击到位。
        # 写完整副本而不是符号链接 —— 链接在浏览器里另存、同步、备份时容易失效。
        latest = PREVIEW / "brief-latest.html"
        latest.write_text(html, encoding="utf-8")
        print(f"→ 固定入口 {latest}")
        notify_brief(today, blocks, latest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
