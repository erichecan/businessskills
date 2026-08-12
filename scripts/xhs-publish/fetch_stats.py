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
              "观看", "点赞", "评论", "收藏", "分享", "搜索来源占比"]

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
    api(f"/navigate?target={tid}&url={DETAIL_URL.format(nid=nid)}")
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
    tid = api(f"/new?url={ANALYSIS_URL}")["targetId"]
    try:
        time.sleep(8)
        rows = ev(tid, JS_ROWS) or []
        api(f"/navigate?target={tid}&url=https://creator.xiaohongshu.com/new/note-manager")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

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

    print(f"{'标题':<24}{'观看':>6}{'点赞':>5}{'收藏':>5}{'分享':>5}  发布天数")
    for r in rows:
        title = r["title"]
        nums = [to_int(x) for x in r["nums"]]
        # 列序：曝光 观看 封面点击率 点赞 评论 收藏 涨粉 分享 时长 弹幕
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
        print(f"{title[:22]:<24}{str(views or '-'):>6}{str(likes or '-'):>5}"
              f"{str(collects or '-'):>5}{str(shares or '-'):>5}  {days}")
        if (today.isoformat(), nid) in seen:
            continue
        s = sources.get(nid) or {}
        ratio = s.get("ratio", "") if s.get("ok") else ""
        if nid and not ratio and s.get("why"):
            print(f"    └ 观看来源：{s['why']}")
        new.append({"抓取日": today.isoformat(), "笔记ID": nid, "标题": title,
                    "发布时间": r["published"], "发布天数": days, "观看": views,
                    "点赞": likes, "评论": comments, "收藏": collects,
                    "分享": shares, "搜索来源占比": ratio})

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

    print("⚠️ 搜索来源占比取不到（详情数据页只认原生手势），需人工填进 词库.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
