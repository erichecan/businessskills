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
import re
import subprocess
import sys
from datetime import date
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
    "com.eric.xhsprobe": "采集探测",
    "com.eric.xhsdata": "发布数据回收",
    "com.eric.xhsaudit": "独立审核",
    "com.eric.xhshealth": "健康检查",
    "com.eric.xhspublish": "半自动发布",
    "com.eric.xhswrite": "写稿 loop",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true", help="只打印不写文件")
    args = ap.parse_args()

    today = date.today().isoformat()
    blocks = [section_collect(today), section_keywords(today), section_drafts(today),
              section_publish(), section_calibrate(), section_launchd()]
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
        html_out = PREVIEW / f"{today.replace('-', '')}-brief.html"
        html_out.write_text(render_html(today, blocks), encoding="utf-8")
        print(f"→ 已写入 {html_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
