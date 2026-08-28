#!/usr/bin/env python3
"""发布数据回流 — 从创作后台内容分析页抓已发布笔记的基础数据。

能自动拿到：观看 / 点赞 / 评论 / 收藏 / 分享 / 发布时间 / noteId
搜索来源占比：走 statistics/note-detail?noteId=<id> 直接进详情页取「观看来源」区块。
列表页那个「详情数据」按钮点不动（Vue 只认 isTrusted 原生手势），但**绕开按钮构造 URL 就能进**。
注意：小红书要数据量够了才生成该区块，量少时页面显示「数据量过少时无法生成分析数据」，
此时返回空字符串，等下次抓取——这是平台限制，不是脚本失败。

产出 发布数据.csv：一篇笔记每天一行，形成时间序列，便于看「发布后 N 天的增长曲线」。

用法：
  python3 fetch_stats.py            # 抓一次，写入 发布数据.csv 并回填词库
  python3 fetch_stats.py --dry-run  # 只打印不落盘
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
STATS = SUCAI / "发布数据.csv"
PROXY = "http://localhost:3456"

STATS_COLS = ["抓取日", "笔记ID", "标题", "发布时间", "发布天数",
              "曝光", "观看", "封面点击率", "点赞", "评论", "收藏", "分享", "搜索来源占比"]

ANALYSIS_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
DETAIL_URL = "https://creator.xiaohongshu.com/statistics/note-detail?noteId={nid}"

JS_SOURCE = """(()=>{const t=document.body.innerText||"";
 const i=t.indexOf("观看来源"), j=t.indexOf("观众画像", i+1);
 if(i<0)return JSON.stringify({ok:false,why:"页面无观看来源区块"});
 const seg=(j>i?t.slice(i,j):t.slice(i,i+400)).replace(/\\n+/g,"|").trim();
 if(/数据量过少|暂时无法分析|无法生成/.test(seg)||seg.replace(/[观看来源|]/g,"").length<4)
   return JSON.stringify({ok:false,why:"数据量未达平台门槛，尚未生成",seg:seg.slice(0,80)});
 const m=seg.match(/搜索[^|]*?([\\d.]+)\\s*%/);
 return JSON.stringify({ok:true,ratio:m?m[1]+"%":"",seg:seg.slice(0,200)})})()"""


def fetch_source_ratio(tid, nid):
    """取单篇的观看来源。拿不到就返回原因，不编数。"""
    api(f"/navigate?target={tid}", DETAIL_URL.format(nid=nid))  # v2.5.3 起 /navigate 改 POST body 传 URL
    time.sleep(9)
    r = ev(tid, JS_SOURCE)
    return r if isinstance(r, dict) else {"ok": False, "why": str(r)}

JS_ROWS = """(()=>{const out=[];
 document.querySelectorAll("tr").forEach(tr=>{
  const cells=[...tr.querySelectorAll("td,th")].map(td=>(td.innerText||"").trim());
  if(cells.length<8)return;
  const head=cells[0]||"";
  const m=head.match(/^(.*?)\\s*发布于\\s*([\\d-]+\\s*[\\d:]*)/s);
  if(!m)return;
  out.push({title:m[1].trim(),published:m[2].trim(),nums:cells.slice(1)});
 });
 return JSON.stringify(out)})()"""

JS_IDS = """(()=>{const out={};
 document.querySelectorAll("[class$=note-card]").forEach(c=>{
  const imp=c.getAttribute("data-impression")||"";
  const m=imp.match(/"noteId":"([0-9a-f]+)"/);
  const t=(c.innerText||"").split("\\n").map(s=>s.trim()).filter(Boolean);
  if(m&&t.length)out[t[0]]=m[1];
 });
 return JSON.stringify(out)})()"""


def api(path, data=None, timeout=40):
    req = urllib.request.Request(f"{PROXY}{path}",
                                 data=data.encode() if data else None,
                                 method="POST" if data else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def ev(tid, js):
    r = api(f"/eval?target={tid}", js)
    v = r.get("value")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def to_int(s):
    s = (s or "").strip()
    if s in ("", "-", "—"):
        return None
    m = re.match(r"^([\d.]+)\s*万$", s)
    if m:
        return int(float(m.group(1)) * 10000)
    return int(s) if s.isdigit() else None


def read_csv(p):
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fetch():
    tid = api("/new", ANALYSIS_URL)["targetId"]  # v2.5.3 起 /new 改 POST body 传 URL
    try:
        time.sleep(8)
        rows = ev(tid, JS_ROWS) or []
        api(f"/navigate?target={tid}", "https://creator.xiaohongshu.com/new/note-manager")
        time.sleep(7)
        ids = ev(tid, JS_IDS) or {}
        # 只对近 30 天的笔记取来源，老笔记平台早已不更新该区块
        sources = {}
        recent = [n for n in ids.values()][:4]
        for nid in recent:
            sources[nid] = fetch_source_ratio(tid, nid)
        return rows, ids, sources
    finally:
        try:
            api(f"/close?target={tid}")
        except Exception:
            pass


def fetch_note_detail(tid, nid):
    """单篇详情页取完整指标 + 观看来源。走 opencli 的 creator-note-detail 适配器。

    ⛔ 2026-08-12 改。原实现（JS_DETAIL_METRICS，「找标签行再往后几行捞数字」）当天写完
    没在真机验过就上了 launchd，21:15 第一次真跑就炸：CDP Proxy 返回 HTTP 400，
    整个 daily_data.sh 的数据回流断在这里。

    换 opencli 而不是修那段 JS 的理由：
      1. CLAUDE.md「联网抓取规则」已把 opencli 定为第 0 优先级
      2. 适配器直接给结构化 section/metric/value，不用猜 DOM 文本顺序，
         站点改版由适配器兜底 —— 这正是那段 JS 反复出错的根源
      3. 自己的笔记不需要 xsec_token，裸 note_id 即可（与 note/comments 不同）

    tid 参数保留只为兼容调用方签名，opencli 路径下不再使用。
    """
    r = subprocess.run(["opencli", "xiaohongshu", "creator-note-detail", nid, "-f", "json"],
                       capture_output=True, text=True, timeout=180)
    try:
        rows = json.loads((r.stdout or "").strip())
    except json.JSONDecodeError:
        return {"ok": False, "why": f"opencli 无有效输出：{(r.stderr or r.stdout or '')[:120]}"}, {}
    if isinstance(rows, dict) and rows.get("ok") is False:
        return {"ok": False, "why": str(rows.get("error"))[:160]}, {}

    # 「观看数」→「观看」：CSV 列名不带「数」，这里对齐 fetch_aged_stats 用的 key。
    # 封面点击率/曝光的 metric 标签名未经真机验证（写这段时 Browser Bridge 未连），
    # 先按最可能的候选名兜底，取不到就留空，不编数——真机跑一次后按 print 的 _debug 校正。
    want = {"观看数": "观看", "点赞数": "点赞", "评论数": "评论",
            "收藏数": "收藏", "分享数": "分享",
            "曝光数": "曝光", "曝光": "曝光",
            "封面点击率": "封面点击率", "点击率": "封面点击率"}
    metrics, ratio = {}, ""
    for row in rows:
        sec, met, val = row.get("section"), row.get("metric"), row.get("value")
        if sec in ("基础数据", "互动数据") and met in want:
            metrics[want[met]] = val
        elif sec == "观看来源" and met == "搜索":
            ratio = val or ""
    src = ({"ok": True, "ratio": ratio} if ratio
           else {"ok": False, "why": "该笔记无搜索来源占比（平台未生成或占比为 0）"})
    return src, metrics


def aged_candidates(max_notes=5):
    """挑发布满 7 天、但 发布数据.csv 里还没有 ≥7 天那一行的笔记，按发布天数从大到小排。

    数据来源词库.csv 的 发布日 + 笔记链接——列表页（fetch() 里的 ANALYSIS_URL）抓不到
    这么老的笔记，只能单篇补，这是预测复盘一直没数据可对账的根因。
    """
    today = date.today()
    covered = set()
    for r in read_csv(STATS):
        try:
            if int((r.get("发布天数") or "0").strip()) >= 7 and (r.get("笔记ID") or "").strip():
                covered.add(r["笔记ID"].strip())
        except ValueError:
            continue

    out = []
    for r in read_csv(CIKU):
        link = (r.get("笔记链接") or "").strip()
        pub = (r.get("发布日") or "").strip()
        if not link or not pub:
            continue
        m = re.search(r"/(?:explore|discovery/item)/([0-9a-zA-Z]+)", link)
        if not m:
            continue
        nid = m.group(1)
        if nid in covered:
            continue
        try:
            days = (today - date.fromisoformat(pub)).days
        except ValueError:
            continue
        if days < 7:
            continue
        out.append((days, nid, (r.get("关键词") or "").strip(), pub))

    out.sort(key=lambda x: -x[0])          # 最老的优先——再不补，窗口更窄
    return out[:max_notes]


def fetch_aged_stats(max_notes=5):
    """给满 7 天但发布数据.csv 里没有对应行的笔记单篇补抓。返回新增的行列表（未落盘）。"""
    cands = aged_candidates(max_notes)
    if not cands:
        return []
    tid = api("/new", ANALYSIS_URL)["targetId"]  # v2.5.3 起 /new 改 POST body 传 URL
    today = date.today()
    new_rows = []
    try:
        for days, nid, kw, pub in cands:
            src, metrics = fetch_note_detail(tid, nid)
            ratio = src.get("ratio", "") if src.get("ok") else ""
            got = {k: v for k, v in metrics.items() if k != "_debug" and v}
            print(f"   补抓「{kw}」（发布 {days} 天）：{got or '未取到任何指标'}"
                  + ("" if src.get("ok") else f"｜来源：{src.get('why','')}"))
            if not got and not ratio:
                if metrics.get("_debug"):
                    print(f"      页面前若干行（用于核对标签文本）：{metrics['_debug'][:12]}")
                continue     # 一个字段都没取到，不写半行假数据
            new_rows.append({"抓取日": today.isoformat(), "笔记ID": nid, "标题": "",
                              "发布时间": pub, "发布天数": days,
                              "曝光": to_int(metrics.get("曝光")),
                              "观看": to_int(metrics.get("观看")),
                              "封面点击率": metrics.get("封面点击率", ""),
                              "点赞": to_int(metrics.get("点赞")),
                              "评论": to_int(metrics.get("评论")), "收藏": to_int(metrics.get("收藏")),
                              "分享": to_int(metrics.get("分享")), "搜索来源占比": ratio,
                              # 下划线前缀 = 只在进程内传递，写 CSV 时被 STATS_COLS 过滤掉。
                              # 用来把 ratio 回写词库（health_check 查的是词库那一列，不是这张表）。
                              "_关键词": kw})
    finally:
        try:
            api(f"/close?target={tid}")
        except Exception:
            pass
    return new_rows


PUB_LOG = SUCAI / "发布日志.csv"
NOTE_URL = "https://www.xiaohongshu.com/explore/{}"


def backfill_note_links(ids: dict) -> None:
    """把创作后台抓到的 noteId 补成「笔记链接」，回填发布日志与词库。

    ⛔ 这一步必须在这里做，auto_publish 做不了：**定时发布的时候笔记还没出去**，
    那时拿不到 noteId，所以它只能在日志里留一句「笔记链接请到创作后台复制后填进词库」。
    结果就是没人填 —— 实测 19 个「已发布」的词里只有 3 个有链接。

    而链接空着不是少一列数据，是**整条预测闭环断在这里**：
    review_prediction 的对账链路是 预测记录 → 关键词 → 词库.笔记链接 → noteId
    → 发布数据，链接一空，40 条预测**一条都对不上账**，
    「为什么没预测准」从建好到现在没产出过一次。

    ids 是本次从后台列表拿到的 {标题: noteId}，标题与发布日志的「标题」列可直接对上
    （实测 16/23 命中；对不上的多是发布前改过标题，留给人工）。
    """
    if not ids or not PUB_LOG.exists():
        return
    rows = read_csv(PUB_LOG)
    if not rows:
        return
    cols = list(rows[0].keys())
    if "笔记链接" not in cols:
        return

    filled = []
    for r in rows:
        title = (r.get("标题") or "").strip()
        if (r.get("笔记链接") or "").strip() or not title:
            continue
        if "✅" not in (r.get("发布") or ""):       # 只补真的发出去了的
            continue
        nid = ids.get(title)
        if not nid:
            continue
        r["笔记链接"] = NOTE_URL.format(nid)
        filled.append((title, r["笔记链接"], (r.get("成稿文件") or "").strip()))

    if not filled:
        return

    tmp = PUB_LOG.with_suffix(".csv.tmp")       # 原子替换，同 auto_publish.backfill_ciku
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, PUB_LOG)
    print(f"\n已补笔记链接 {len(filled)} 条 → 发布日志.csv")

    sys.path.insert(0, str(Path(__file__).parent))
    from auto_publish import backfill_ciku          # 复用，别写第二份词库写回
    for title, link, name in filled:
        print(f"   {backfill_ciku(title, link, name)}")


def backfill_ciku_ratio(aged_rows):
    """把补抓到的搜索来源占比回写 词库.csv 的同名列。

    ⛔ 2026-08-12 补的链路断点。此前 fetch_aged_stats 抓到 ratio 只写 发布数据.csv，
    而 health_check 的「发布满 7 天未回填搜索来源占比」查的是 **词库.csv** 那一列
    （health_check.py:138）。两张表从来没打通，所以这条告警从存在起就不可能消除 ——
    数据一直抓得到，只是没送到被检查的那张表里。

    只写非空 ratio：平台对低曝光笔记不生成观看来源，那种情况留空是事实，
    用空值覆盖已有数据反而是倒退。
    """
    have = {r["_关键词"]: r["搜索来源占比"] for r in aged_rows
            if r.get("_关键词") and (r.get("搜索来源占比") or "").strip()}
    if not have:
        return
    rows = read_csv(CIKU)
    if not rows:
        return
    cols = list(rows[0].keys())
    if "搜索来源占比" not in cols:
        print("   ⚠️ 词库.csv 无「搜索来源占比」列，跳过回写")
        return

    hit = 0
    for r in rows:
        kw = (r.get("关键词") or "").strip()
        if kw in have and not (r.get("搜索来源占比") or "").strip():
            r["搜索来源占比"] = have[kw]
            hit += 1
    if not hit:
        return
    tmp = CIKU.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    os.replace(tmp, CIKU)
    print(f"   已回写词库搜索来源占比 {hit} 条：" +
          "、".join(f"{k}={v}" for k, v in have.items()))


def migrate_stats_header():
    """把旧表头（缺曝光/封面点击率两列）的 发布数据.csv 迁移到新列序。

    2026-08-27 补：这两列此前一直被解析出来又被丢弃，现在开始落盘。
    旧文件的表头还是老的，若不迁移就直接 append 新列，会导致 DictWriter
    写出的新行比磁盘上的表头多两列，读取时全部错位。
    """
    if not STATS.exists():
        return
    with STATS.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        old_cols = reader.fieldnames or []
        rows = list(reader)
    if old_cols == STATS_COLS:
        return
    tmp = STATS.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STATS_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in STATS_COLS})
    os.replace(tmp, STATS)
    print(f"已迁移 发布数据.csv 表头（补 曝光/封面点击率 两列，历史 {len(rows)} 行留空）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dry_run:
        migrate_stats_header()

    try:
        rows, ids, sources = fetch()
    except Exception as e:
        print(f"取数失败：{e}\n先确认 CDP 代理在跑，且 Chrome 已登录创作后台", file=sys.stderr)
        return 1
    if not rows:
        print("内容分析页没解析出任何笔记行——页面结构可能已变", file=sys.stderr)
        return 1

    today = date.today()
    ciku = {r["关键词"].strip(): r for r in read_csv(CIKU)}
    seen = {(r["抓取日"], r["笔记ID"]) for r in read_csv(STATS)}
    new = []

    print(f"{'标题':<24}{'观看':>6}{'点击率':>7}{'点赞':>5}{'收藏':>5}{'分享':>5}  发布天数")
    for r in rows:
        title = r["title"]
        raw = [x.strip() if isinstance(x, str) else x for x in r["nums"]]
        nums = [to_int(x) for x in raw]
        # 列序：曝光 观看 封面点击率 点赞 评论 收藏 涨粉 分享 时长 弹幕
        exposure = nums[0] if len(nums) > 0 else None
        ctr = raw[2] if len(raw) > 2 and raw[2] not in ("", "-", "—") else ""
        views, likes, comments, collects, shares = (
            nums[1] if len(nums) > 1 else None, nums[3] if len(nums) > 3 else None,
            nums[4] if len(nums) > 4 else None, nums[5] if len(nums) > 5 else None,
            nums[7] if len(nums) > 7 else None)
        pub = r["published"][:10]
        try:
            days = (today - date.fromisoformat(pub)).days
        except ValueError:
            days = ""
        nid = ids.get(title, "")
        print(f"{title[:22]:<24}{str(views or '-'):>6}{ctr or '-':>7}{str(likes or '-'):>5}"
              f"{str(collects or '-'):>5}{str(shares or '-'):>5}  {days}")
        if (today.isoformat(), nid) in seen:
            continue
        s = sources.get(nid) or {}
        ratio = s.get("ratio", "") if s.get("ok") else ""
        if nid and not ratio and s.get("why"):
            print(f"    └ 观看来源：{s['why']}")
        new.append({"抓取日": today.isoformat(), "笔记ID": nid, "标题": title,
                    "发布时间": r["published"], "发布天数": days, "曝光": exposure,
                    "观看": views, "封面点击率": ctr, "点赞": likes, "评论": comments,
                    "收藏": collects, "分享": shares, "搜索来源占比": ratio})

    if args.dry_run:
        print(f"\n[dry-run] 将写入 {len(new)} 行，未落盘")
        return 0
    if new:
        exists = STATS.exists()
        with STATS.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=STATS_COLS)
            if not exists:
                w.writeheader()
            for r in new:
                w.writerow({c: r.get(c, "") for c in STATS_COLS})
        print(f"\n已写入 发布数据.csv：{len(new)} 行")
    else:
        print("\n今天已抓过，无新增")

    backfill_note_links(ids)

    # 2026-08-12：补满 7 天数据的老笔记，单篇详情页取——列表页只显示最近几天，
    # 这是预测复盘一直没数据可对账的根因。见 fetch_aged_stats() 顶部注释：
    # 首次真机运行前未验证过 DOM，出问题看 print 里的原始行去改标签文本。
    if args.dry_run:
        preview = aged_candidates()
        print(f"\n[dry-run] {len(preview)} 篇满 7 天缺数据，未实抓")
    else:
        aged = fetch_aged_stats()
        if aged:
            exists = STATS.exists()
            with STATS.open("a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=STATS_COLS)
                if not exists:
                    w.writeheader()
                for r in aged:
                    w.writerow({c: r.get(c, "") for c in STATS_COLS})
            print(f"已补抓满 7 天数据 {len(aged)} 条 → 发布数据.csv")
            backfill_ciku_ratio(aged)
        else:
            print("\n没有满 7 天且缺数据的笔记需要补抓（或本轮一条都没取到，见上面的排查提示）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
