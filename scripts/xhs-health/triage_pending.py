#!/usr/bin/env python3
"""待人工分诊 —— 把「等人看」的那一档接管过来，顺手把稿里的素材捞出来。

## 为什么需要这个

independent_audit 的分档里，55-74 分落进「待人工」，定义是
「短板是结构性的，改一句救不回来，**值不值得再投入得由人判断**」。
问题是没人判 —— 到 2026-08-13 已经积压 11 篇，最早的来自 08-02。
写稿花掉的额度、探测花掉的配额，全停在这一档不动。

Eric 2026-08-13 的指示把这个判断权交出来了：
「待人工这里还是你自己看不要我来看了。如果质量不行就退回，
  但是关键词、原话、案例还是提炼出来好，以后能给其他文章使用」

所以这个脚本做两件事，第二件比第一件重要：

  ① 分诊：判定「返工」还是「归档」，写回审核记录，让稿子离开待人工这潭死水
  ② **沉淀**：不管稿子生死，把里面能被别的稿复用的东西捞进 话术复用库.csv

②才是关键。一篇 72 分的稿被归档，不代表里面那句「评委记住的不是这道题答没答对，
是你这个人」没价值 —— 它是创作产物，不是从库里取来的素材，稿子一归档就随之埋掉了。
探测数据、评论区原话进库有专门链路，唯独**写出来的话术**没有回收口，这里补上。

## 判断权边界

「返工 vs 归档」是本脚本唯一的判断项，其余都是抄录：
分数、评级、红线一律沿用最近一次独立审核的结论，不重新打分 ——
重新打分等于让分诊变成第二次审核，两套分数并存会让后面的校准无从对账。

## 用法

  python3 triage_pending.py --dry-run        # 只看会怎么判，不写任何文件
  python3 triage_pending.py --limit 3        # 最多处理 3 篇
  python3 triage_pending.py --engine claude  # 强制走 Claude（默认 Gemini，不花订阅额度）
"""
import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemini_cli  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
REPORT_DIR = SUCAI / "审核报告"
REUSE_LIB = SUCAI / "话术复用库.csv"
ARCHIVE_DIR = SUCAI / "归档稿"

REUSE_COLS = ["日期", "来源成稿", "来源处置", "关键词", "类型", "内容", "适用场景"]
# 分诊写进审核记录时用的审核方名。不叫「独立审核」——那是裁判的名字，
# 冒用会污染 calibrate_audit 的样本（它按审核方筛行反推标准）。
AUDITOR = "自动分诊"
TERMINAL = {"发布", "归档", "返工"}


def read_csv(p):
    if not p.exists():
        return []
    return list(csv.DictReader(p.open(encoding="utf-8-sig")))


def pending_drafts():
    """最新一条判定是「待人工」的稿。

    只看最后一条：一篇稿可能先被判待人工、后来又被分诊改成返工，
    按「存在待人工行」筛会把已处理的又捞回来，每天重判一次。
    """
    rows = read_csv(AUDIT_LOG)
    latest = {}
    for r in rows:
        who = (r.get("审核方") or "").strip()
        if who in ("独立审核", "人工放行", AUDITOR):
            latest[(r.get("成稿文件") or "").strip()] = r
    return [(n, r) for n, r in sorted(latest.items())
            if (r.get("处置") or "").strip() == "待人工" and n]


def draft_path(name):
    """成稿可能已经被挪进 归档稿/。两处都找，找不到返回 None。"""
    for p in (SUCAI / name, ARCHIVE_DIR / name):
        if p.exists():
            return p
    return None


def rework_rounds(name):
    """这篇被返工过几轮 —— 判「还值不值得再投入」时，投入了多少是硬信息。"""
    return sum(1 for r in read_csv(AUDIT_LOG)
               if (r.get("成稿文件") or "").strip() == name
               and (r.get("处置") or "").strip() == "返工")


def latest_report(name):
    """最近一次审核报告全文。分诊要看的是「扣在哪」，不是分数本身。"""
    stem = name.removeprefix("成稿_").removesuffix(".md")
    hits = sorted(REPORT_DIR.glob(f"*{stem}*"), key=lambda p: p.stat().st_mtime)
    return hits[-1].read_text(encoding="utf-8")[:6000] if hits else ""


PROMPT = """你在给一批卡住的小红书成稿做分诊。账号定位＝**职场**，只对职场读者说话。

这篇稿的独立审核结论是「待人工」，含义是：短板可能是结构性的、改一句救不回来，
值不值得再投入需要判断。现在由你判断。

【判据】
- 返工：短板是**可定点修复**的（标题落点、首图原句、关键词密度、CTA 形式、开头切入）。
        改动集中在一两处，改完有机会上 85 分。
- 归档：短板是**结构性**的（选题本身没有搜索价值、答的不是读者搜的那个问题、
        通篇没有可用素材、或该词的搜索位本身没人互动而稿子也无差异化角度）。
        已返工多轮仍在同一档，也算结构性 —— 再投入是浪费。

⛔ 不要重新打分。分数与红线沿用下面给出的独立审核结论。
⛔ 不得因为「搜索位上的人群不是职场人」而判归档 —— 账号只服务职场读者，
   同一个词的职场解法就是本账号的正确答案。

【素材提炼 —— 这一项比判定更重要】
不管你判返工还是归档，都要把这篇稿里**能被别的文章直接复用**的东西挑出来。
只挑真正可迁移的，宁少勿滥；没有就给空数组。三类：
- 话术：可以照着说的句子/句式模板（如「我先说结论，细节会后追问」）
- 记忆句：≤15 字、可截图、脱离本文也成立的一句
- 框架：正文里的分类结构（如「打断分三种：抢答型/纠偏型/时间型」）

【成稿文件】{name}
【独立审核】总分 {score} · 评级 {grade} · 红线 {redline}
【已返工轮数】{rounds}
【审核报告摘录】
{report}

【成稿全文】
{draft}

只输出 JSON，不要任何其他文字：
{{"verdict": "返工|归档",
  "reason": "一句话，说清判据落在哪一条",
  "reusable": [{{"类型": "话术|记忆句|框架", "内容": "...", "适用场景": "什么情况下能用上"}}]}}
"""


def ask(prompt, engine):
    if engine != "claude" and gemini_cli.available():
        try:
            return gemini_cli.run(prompt, temperature=0.2)
        except gemini_cli.QuotaExhausted as e:
            print(f"   ⏬ Gemini 额度已满：{e}")
            print("   · 分诊不急，本轮跳过，下次再来（要现在就跑加 --engine claude，会花订阅额度）")
            return ""
        except Exception as e:                              # noqa: BLE001
            print(f"   ⚠️ Gemini 调用失败：{e}")
            return ""
    import subprocess
    from headless_cli import HEADLESS_FLAGS, ensure_cwd
    r = subprocess.run([str(Path.home() / ".local/bin/claude"), *HEADLESS_FLAGS, "-p", prompt],
                       cwd=str(ensure_cwd()), capture_output=True, text=True, timeout=600)
    return (r.stdout or "").strip()


def parse(out):
    """模型爱把 JSON 包在 ``` 里，也爱在前面加一句话。取第一个完整对象。"""
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if d.get("verdict") not in ("返工", "归档"):
        return None
    d.setdefault("reusable", [])
    d.setdefault("reason", "")
    return d


def append_audit(base, verdict, reason):
    rows = read_csv(AUDIT_LOG)
    cols = list(rows[0].keys())
    row = {c: "" for c in cols}
    row.update(base)
    row.update({"日期": date.today().isoformat(), "审核方": AUDITOR, "处置": verdict,
                "备注": f"自动分诊（Eric 2026-08-13 授权）：{reason}"
                        f"｜沿用独立审核 {base.get('总分')}分（{base.get('评级')}）"})
    with AUDIT_LOG.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=cols).writerow(row)


def append_reuse(name, verdict, keyword, items):
    if not items:
        return 0
    exists = REUSE_LIB.exists()
    today = date.today().isoformat()
    with REUSE_LIB.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REUSE_COLS)
        if not exists:
            w.writeheader()
        for it in items:
            w.writerow({"日期": today, "来源成稿": name, "来源处置": verdict,
                        "关键词": keyword, "类型": (it.get("类型") or "").strip(),
                        "内容": (it.get("内容") or "").strip(),
                        "适用场景": (it.get("适用场景") or "").strip()})
    return len(items)


def keyword_of(path):
    m = re.search(r"关键词来源[^「]*「([^」]+)」", path.read_text(encoding="utf-8")[:2000])
    return m.group(1).strip() if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--engine", default="gemini", choices=["gemini", "claude"])
    args = ap.parse_args()

    pend = pending_drafts()
    if not pend:
        print("待人工队列为空。")
        return 0
    print(f"待人工 {len(pend)} 篇，本次处理 {min(len(pend), args.limit)} 篇"
          f"（引擎：{args.engine}）\n")

    done = collected = 0
    for name, base in pend[:args.limit]:
        p = draft_path(name)
        if not p:
            print(f"⚠️ {name}：文件不在素材库也不在归档稿，跳过")
            continue
        kw = keyword_of(p)
        prompt = PROMPT.format(
            name=name, score=base.get("总分"), grade=base.get("评级"),
            redline=base.get("红线") or "无", rounds=rework_rounds(name),
            report=latest_report(name) or "（无审核报告文件）",
            draft=p.read_text(encoding="utf-8")[:8000])
        d = parse(ask(prompt, args.engine))
        if not d:
            print(f"⚠️ {name}：模型没给出可解析的判定，留在队列里")
            continue

        items = d["reusable"]
        print(f"{'📝' if d['verdict'] == '返工' else '🗄'} {name}")
        print(f"    → {d['verdict']}：{d['reason']}")
        for it in items:
            print(f"    · [{it.get('类型')}] {str(it.get('内容'))[:52]}")
        if args.dry_run:
            continue
        append_audit(base, d["verdict"], d["reason"])
        collected += append_reuse(name, d["verdict"], kw, items)
        done += 1

    if args.dry_run:
        print("\n[dry-run] 未写入任何文件")
    else:
        print(f"\n完成 {done} 篇，沉淀可复用素材 {collected} 条 → {REUSE_LIB.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
