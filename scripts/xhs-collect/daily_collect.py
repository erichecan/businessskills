#!/usr/bin/env python3
"""每日采集 —— 供给端的源头。给整条流水线送新笔记、新词、新热度。

## 为什么现在才有这个脚本

到 2026-08-14 为止，运行日志.csv 里那些 run1-run4 **全是人手工跑出来的**：
上一个会话在 `xhs/素材库/.run3_work/` 下临时写 append_runlog.py / update_pool.py /
update_kudb.py，跑完就扔。backfill.py 的注释写着「运行日志由采集任务独占」，
可仓库里从来没有那个采集任务。于是「全自动」链条最上游是断的 ——
探词、写稿、审核、发布全都自动了，唯独喂给它们的原料要人开一次会话去搬。

Eric 2026-08-14：「做成定时任务」。

## 这一版做什么、不做什么

做：选词 → 搜索抓取 → 相关性过滤 → 与记忆库去重 → 入库 → 更新关键词池 → 写运行日志。
    也就是手工流程里**每轮都在做、且规则明确**的那条主干。

不做（说明理由，别让后人以为忘了）：
  · **新词收割**：手工流程的新词来自搜索页的「大家都在搜 / 相关搜索 / 筛选标签」，
    而 opencli 没有对应命令（只有 search/comments/note）。硬爬那些页面元素等于
    退回 DOM 选择器，站点一改版就废 —— 这正是当初迁到 opencli 要甩掉的东西。
    改用「从抓到的标题里提炼候选词」这条路，另开一步做，不混在主干里。
  · **评论区原话**：已经由 probe 链路覆盖（probe_opencli 抓 note 正文 + 评论，
    harvest_cases 再搬进案例库）。这里再抓一遍是重复劳动，还多烧一轮风控额度。

## 判断权边界

只有「这条笔记是不是职场/求职内容」这一项交给模型，其余全是代码算的：
选哪些词、命中率、要不要升级、记忆库累计 —— 都是可复现的规则。
相关性这一项非交给模型不可：run3 那轮「高情商破局」原始命中 16/20，
人工剔掉 10 条泛人性心理内容后只剩 6 条，靠关键词匹配做不到这种判断。

用法：
  python3 daily_collect.py --dry-run       # 只抓不写，看会收进来什么
  python3 daily_collect.py --limit 4       # 本轮只跑 4 个词
  python3 daily_collect.py --engine claude # 相关性过滤改用 Claude（默认 Gemini，免费）
"""
import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "xhs-probe"))
import gemini_cli  # noqa: E402
import probe  # noqa: E402  复用 parse_likes
import probe_opencli  # noqa: E402  复用 oc()（含 opencli 路径解析）

REPO = SCRIPTS.parent
SUCAI = REPO / "xhs" / "素材库"
MEMORY = SUCAI / "职场面试_记忆库.csv"
POOL = SUCAI / "关键词池.csv"
RUN_LOG = SUCAI / "运行日志.csv"

PER_KEYWORD = 20      # 每词抓几条。手工流程一直是 20，改了命中率就没法跟历史比
KEYWORDS_PER_RUN = 8  # 4 种子 + 2 长空档活跃 + 2 候选
SEED_N, AGED_N, CAND_N = 4, 2, 2
PROMOTE_RATE = 0.40   # 候选词命中率达标即升活跃。手工流程用的就是 40%
# ⛔ 2026-08-16 从「2.5 秒固定」改成随机区间（Eric：调时间和频率，避开风控）。
#
# 起因：这天主站搜索被风控，`opencli xiaohongshu search 面试` 连常见词都返回空数组，
# 而创作者中心 whoami 正常 —— 是主站单独被限。
#
# 回头看请求特征，daily_collect 是最像机器的一环：
#   · 间隔 2.5 秒**固定**，没有任何抖动
#   · 一轮 8 词 × 每词 20 条，全程约 20 秒打完 160 条抓取
# 对照同仓库的 probe.py：DELAY_BETWEEN_KEYWORDS = (45, 90) 秒**随机**。
# 同一个账号、同一个 Chrome，两个脚本的节奏差了 20-35 倍。
#
# 区间取 (20, 45)：与 ximalaya 那条线的 upload_delay 同一个量级
# （那边注释写着「反封控：和小红书那套同一个道理，别连着传」）。
# 一轮 8 词因此从 ~20 秒拉到 2.5-6 分钟 —— 慢，但这一步本来就不赶时间。
GAP_RANGE = (20, 45)

# 开跑前的随机错峰上限（秒）。launchd 掐整点触发，规律性本身就是特征。
# 15 分钟够把「每天 09:00 准时一串搜索」摊成「09:00-09:15 之间某个时刻」，
# 又不会跟下一个任务的时段撞上（改版后搜索类任务间隔 ≥2.5 小时）。
JITTER_MAX = 15 * 60


def gap_sleep():
    """两次 opencli 之间随机停一会儿。随机比固定重要 —— 固定间隔本身就是特征。"""
    time.sleep(random.uniform(*GAP_RANGE))

MEMORY_COLS = ["标题", "URL", "来源", "关键词", "首次收录日期", "热度"]
RUN_LOG_COLS = ["日期", "轮次", "跑的关键词数", "总抓取条数(估)", "本轮新增条数",
                "记忆库累计", "小红书状态", "连续0新增轮数", "告警", "备注"]


def read_csv(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def write_csv(p, rows, cols):
    """原子替换。关键词池/记忆库可能正被 probe 或写稿读，直接 open('w') 会有
    一段「文件被截断、只写了一半」的窗口，读者这时读到的是残缺 CSV。"""
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    tmp.replace(p)


def note_id(url):
    m = re.search(r"/(?:search_result|explore|discovery/item)/([0-9a-f]{24})", url or "")
    return m.group(1) if m else ""


def memory_keys(rows):
    """去重键：note_id 优先、回退标题。

    记忆库 4560 行里只有 2279 行的 URL 带笔记 ID（早期收录的 URL 存的是搜索页地址），
    所以两种键都得认。标题重复的有 209 组 —— 同一篇被不同关键词收到过，
    按标题去重会把它们当成一条，这正是我们要的（记忆库记的是「这篇见过没有」）。
    """
    ids, titles = set(), set()
    for r in rows:
        nid = note_id(r.get("URL") or "")
        if nid:
            ids.add(nid)
        t = (r.get("标题") or "").strip()
        if t:
            titles.add(t)
    return ids, titles


def pick_keywords(pool, limit):
    """选词：4 固定种子 + 2 长空档活跃复查 + 2 候选。

    「长空档活跃复查」是手工流程反复验证过的规律：距上次运行越久的活跃词，
    本轮命中率越高。2026-08-13 run3 实测 —— 长空档的两个词命中 30.3% / 60.0%，
    而当天第 3 次复查的种子词只有 30% / 5% / 0% / 15%。
    所以这里按「最近运行」升序取最久没跑的，不是随便挑。
    """
    def last_run(r):
        return (r.get("最近运行") or "").strip() or "0000-00-00"

    # ⛔ 2026-08-16：种子也要按「最近运行」轮换，不能再 seeds[:4] 硬取前四。
    #
    # 原来是无序切片，于是池子里排在前面的那 4 个种子**每轮必投、永远只投它们**。
    # 后果有两层：
    #   ① 那 4 个词（职场表达/面试技巧/面试什么话该说/面试什么话不能说）各跑了
    #      169-170 次，命中率掉到 0%/0%/5%，备注里自己写着「饱和」—— 还在每天投。
    #   ② 3/4 是面试词，等于整个采集漏斗 75% 的入口是面试。这就是选题面收窄的根因：
    #      已发布 22 篇里 14 篇面试/答辩/谈薪，而记忆库中非面试类占 41%、
    #      热度天花板高一个数量级（治同事 55.9万 vs 面试最高 8.4万）。
    # 新加的种子若还按无序切片，会永远排在后面、一次都投不出去。
    seeds = sorted([r for r in pool if (r.get("类型") or "").strip() == "种子"],
                   key=last_run)
    active = sorted([r for r in pool if (r.get("类型") or "").strip() == "活跃"],
                    key=last_run)
    cands = sorted([r for r in pool if (r.get("类型") or "").strip() == "候选"],
                   key=last_run)

    picked = seeds[:SEED_N] + active[:AGED_N] + cands[:CAND_N]
    # 池子里某一类不够时，用活跃词补满 —— 空转一轮比少跑几个词更亏
    if len(picked) < limit:
        rest = [r for r in active[AGED_N:] if r not in picked]
        picked += rest[:limit - len(picked)]
    return picked[:limit]


RELEVANCE_PROMPT = """下面是小红书搜索结果的标题列表。账号定位＝**职场**，
只收职场 / 求职 / 面试 / 汇报 / 晋升 / 谈薪 / 同事领导关系 这类内容。

要剔掉的是：泛人性心理、成功学、情感恋爱、生活方式、学习方法、纯英语学习、
读书笔记、以及和职场没有具体关联的鸡汤。判断看**这条笔记本身讲什么**，
不看它是从哪个关键词搜出来的。

拿不准时保留（宁可多收，后面还有别的过滤）。

只输出 JSON，不要任何其他文字：{{"keep": [保留的序号数组]}}

{items}
"""


def filter_relevant(titles, engine):
    """相关性过滤。返回要保留的下标集合；模型不可用时全保留（宁可多收，不空转）。"""
    if not titles:
        return set()
    items = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    prompt = RELEVANCE_PROMPT.format(items=items)
    out = ""
    if engine != "claude" and gemini_cli.available():
        try:
            out = gemini_cli.run(prompt, temperature=0.1)
        except gemini_cli.QuotaExhausted as e:
            print(f"   ⏬ Gemini 额度已满（{e}），本轮不过滤，全部收录")
            return set(range(len(titles)))
        except Exception as e:                              # noqa: BLE001
            print(f"   ⚠️ 相关性过滤失败（{e}），本轮不过滤，全部收录")
            return set(range(len(titles)))
    else:
        import subprocess
        from headless_cli import build_argv, ensure_cwd
        r = subprocess.run(build_argv(Path.home() / ".local/bin/claude", prompt),
                           cwd=str(ensure_cwd()),
                           capture_output=True, text=True, timeout=600)
        out = (r.stdout or "").strip()
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        print("   ⚠️ 过滤结果解析不出来，本轮全部收录")
        return set(range(len(titles)))
    try:
        keep = json.loads(m.group()).get("keep", [])
    except json.JSONDecodeError:
        return set(range(len(titles)))
    return {i for i in keep if isinstance(i, int) and 0 <= i < len(titles)}


def collect_one(kw, seen_ids, seen_titles):
    """抓一个词。返回 (抓到几条, 新增行列表, 被过滤掉几条)。"""
    hits = probe_opencli.oc(["search", kw, "--limit", str(PER_KEYWORD)])
    if not hits or not isinstance(hits, list):
        return 0, [], 0

    fresh = []
    for h in hits:
        title = (h.get("title") or "").strip()
        url = (h.get("url") or "").strip()
        if not title:
            continue
        nid = note_id(url)
        if (nid and nid in seen_ids) or title in seen_titles:
            continue
        fresh.append({"标题": title, "URL": url, "来源": "XHS", "关键词": kw,
                      "首次收录日期": date.today().isoformat(),
                      "热度": (h.get("likes") or "").strip()})
    return len(hits), fresh, 0


def run_no(today):
    """今天第几轮。运行日志是唯一真相 —— 别用时间去猜轮次，
    任务补跑或漏跑时时间和轮次就对不上了。"""
    n = sum(1 for r in read_csv(RUN_LOG) if (r.get("日期") or "").strip() == today)
    return f"run{n + 1}"


def update_pool(pool, stats, today):
    """回写关键词池：运行次数 +1、命中率、最近运行、平均热度；候选达标则升活跃。"""
    promoted = []
    by_kw = {(r.get("关键词") or "").strip(): r for r in pool}
    for kw, s in stats.items():
        r = by_kw.get(kw)
        if not r:
            continue
        try:
            r["运行次数"] = str(int((r.get("运行次数") or "0").strip() or 0) + 1)
        except ValueError:
            r["运行次数"] = "1"
        rate = (s["new"] / s["total"] * 100) if s["total"] else 0
        note = f"{s['run']}({today})_{s['new']}/{s['total']}={rate:.1f}%"
        r["命中率"] = f"{note} | {(r.get('命中率') or '')[:600]}"
        r["累计新增条数"] = f"{note}; {(r.get('累计新增条数') or '')[:600]}"
        r["最近运行"] = today
        if s["likes"]:
            avg = sum(s["likes"]) / len(s["likes"])
            r["平均热度"] = f"约{avg:.0f}（{len(s['likes'])}条样本）"
        # 候选升活跃：够格就升，不留给人判断 —— 这条规则本来就是数字说了算
        if (r.get("类型") or "").strip() == "候选" and s["total"] and \
                s["new"] / s["total"] >= PROMOTE_RATE:
            r["类型"] = "活跃"
            promoted.append(f"{kw}({rate:.1f}%)")
    return promoted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=KEYWORDS_PER_RUN)
    ap.add_argument("--engine", default="gemini", choices=["gemini", "claude"])
    ap.add_argument("--no-jitter", action="store_true",
                    help="跳过开跑前的随机延迟（手工调试用；定时任务别加）")
    args = ap.parse_args()

    # ⛔ 开跑前先随机等一会儿（2026-08-16 加）。
    # launchd 是**掐着整点**触发的，于是每天 08:00:0x / 12:00:0x 准时来一串搜索 ——
    # 这个规律性本身就是特征，间隔随机化解决不了它。
    # dry-run 和手工调试不等，免得每次验证都干坐几分钟。
    if not (args.dry_run or args.no_jitter):
        wait = random.uniform(0, JITTER_MAX)
        print(f"（错峰等待 {wait / 60:.1f} 分钟再开跑，避开整点规律）", flush=True)
        time.sleep(wait)

    today = date.today().isoformat()
    pool = read_csv(POOL)
    if not pool:
        print("⛔ 关键词池为空，没法选词", file=sys.stderr)
        return 2
    mem = read_csv(MEMORY)
    seen_ids, seen_titles = memory_keys(mem)
    picked = pick_keywords(pool, args.limit)
    rn = run_no(today)
    print(f"===== {today} {rn} 采集开始 · {len(picked)} 词 · 记忆库 {len(mem)} 条 =====")

    all_new, stats, total_hit, failed = [], {}, 0, []
    for i, r in enumerate(picked, 1):
        kw = (r.get("关键词") or "").strip()
        kind = (r.get("类型") or "").strip()
        print(f"[{i}/{len(picked)}] {kw}（{kind}）")
        try:
            n, fresh, _ = collect_one(kw, seen_ids, seen_titles)
        except Exception as e:                              # noqa: BLE001
            print(f"    ⚠️ 抓取失败：{e}")
            failed.append(kw)
            continue
        if not n:
            print("    → 0 条（搜索无结果或登录态失效）")
            failed.append(kw)
            continue
        total_hit += n

        kept = filter_relevant([x["标题"] for x in fresh], args.engine) if fresh else set()
        dropped = len(fresh) - len(kept)
        fresh = [x for i2, x in enumerate(fresh) if i2 in kept]
        # 本轮内也要去重：8 个词的结果之间会重叠
        for x in fresh:
            nid = note_id(x["URL"])
            if nid:
                seen_ids.add(nid)
            seen_titles.add(x["标题"])
        all_new += fresh
        likes = [v for v in (probe.parse_likes(x["热度"]) for x in fresh) if v]
        stats[kw] = {"total": n, "new": len(fresh), "likes": likes, "run": rn}
        print(f"    → 抓 {n} 条，新增 {len(fresh)} 条（{len(fresh) / n * 100:.1f}%）"
              f"{f'，相关性过滤剔除 {dropped} 条' if dropped else ''}")
        if i < len(picked):
            gap_sleep()

    if args.dry_run:
        print(f"\n[dry-run] 会新增 {len(all_new)} 条，未写入任何文件")
        return 0

    promoted = update_pool(pool, stats, today)
    write_csv(POOL, pool, list(pool[0].keys()))
    write_csv(MEMORY, mem + all_new, MEMORY_COLS)

    prev0 = 0
    for r in reversed(read_csv(RUN_LOG)):
        if (r.get("本轮新增条数") or "").strip() == "0":
            prev0 += 1
        else:
            break
    zero_runs = prev0 + 1 if not all_new else 0
    # 全部词都抓不到 = 登录态或 opencli 出问题，跟「抓到了但没新东西」是两回事
    broken = len(failed) == len(picked)
    note = (f"{len(picked)}词(" + "/".join(
        f"{(r.get('关键词') or '')[:14]}:{stats.get((r.get('关键词') or '').strip(), {}).get('new', 0)}"
        f"/{stats.get((r.get('关键词') or '').strip(), {}).get('total', 0)}" for r in picked) + ")")
    if promoted:
        note += f"；候选升活跃：{'、'.join(promoted)}"
    if failed:
        note += f"；抓取失败：{'、'.join(failed)}"
    write_csv(RUN_LOG, read_csv(RUN_LOG) + [{
        "日期": today, "轮次": rn, "跑的关键词数": str(len(picked)),
        "总抓取条数(估)": str(total_hit), "本轮新增条数": str(len(all_new)),
        "记忆库累计": str(len(mem) + len(all_new)),
        "小红书状态": "登录墙/抓取失败" if broken else "已登录连接正常",
        "连续0新增轮数": str(zero_runs),
        "告警": "是" if (broken or zero_runs >= 3) else "否", "备注": note,
    }], RUN_LOG_COLS)

    print(f"\n新增 {len(all_new)} 条 → 记忆库累计 {len(mem) + len(all_new)}")
    if promoted:
        print(f"候选升活跃：{'、'.join(promoted)}")
    if broken:
        print("⛔ 所有词都没抓到 —— 多半是登录态失效，"
              "跑 `opencli xiaohongshu search 测试 --limit 1` 验一下", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
