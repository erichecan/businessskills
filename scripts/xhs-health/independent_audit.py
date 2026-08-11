#!/usr/bin/env python3
"""独立审核（headless claude）— 裁判与运动员分离。

找出最近 3 天内没有审核记录的 成稿_*.md，用 headless `claude -p` 按
eric-xhs-audit 标准做第三方审核（只给成稿文本，不给写作过程），
把 CSV 行追加进 审核记录.csv，完整报告存 素材库/审核报告/。

由 com.eric.xhsaudit LaunchAgent 每天 09:05 触发；也可手动运行。
"""
import argparse
import csv
import io
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_limits import WEEKLY, classify_limit, limit_banner  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
REPORT_DIR = SUCAI / "审核报告"
CLAUDE = Path.home() / ".local/bin/claude"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 给模型的列（不含审核方——那一列由代码填，模型无权自称是谁）
# 维度列不带分值——口径切换时满分会变
# （搜索流 20/20/15/10/10/15/10，推荐流 20/20/15/15/13/7/10；2026-08-08 两条流都把
#  CTA 提到 10 分，见 eric-xhs-audit/SKILL.md 与 知识框架.md 第十六节）
MODEL_HEADER = ("日期,成稿文件,总分,评级,口径,选题,标题,首图,开头,正文,"
                "可信度,CTA,红线,处置,备注")
AUDITOR_COL_INDEX = 13  # 红线之后、处置之前
HEADER = MODEL_HEADER.replace("红线,处置", "红线,审核方,处置")
DISPOSITION_COL_INDEX = 14  # 插入审核方之后，处置就落在这一列


def decide_disposition(score, redline):
    """处置由代码算，不让模型自己填 —— 和「审核方」「口径」两列同一个道理（D7）。

    ⛔ 这是 2026-08-05 查出的真 bug。原先 prompt 只写「处置用 发布/待人工/归档」，
    没给任何分档规则，模型就自己发挥：`成稿_2026-08-05_干最多的常被刷.md` 第 1 轮
    拿到 **85 分 绿 红线无**（refine_loop 的过线线正好是 85），审核员却写
    「85分擦线故转人工」判了「待人工」。loop 的过线条件是 分数≥85 且 无红线 且
    **处置=发布**，于是这篇明明达标的稿被判未过线、继续返工，第 2 轮掉到 84 分，
    最后当作失败归档。评级表（SKILL.md）写得清清楚楚 ≥85 = 🟢 可发，
    但那张表管的是「评级」列，没人把它接到「处置」列上。

    接上之后 loop 才真的可能过线 —— 在此之前，只要审核员觉得「擦线」，
    这套 loop 在结构上就永远出不了一篇可发的稿。

    ── 分档为什么是这四档（阈值由 45 篇独立审核实测标定，不是拍脑袋）──

    历史分布：中位 81、最高 89、**从没有一篇到过 90**。
      85-89 → 10 篇（全部无红线）    80-84 → 14 篇（全部无红线）
      70-79 → 17 篇（13 篇无红线）   <70  →  4 篇

    每一档回答的是同一个问题：**下一步谁来动手。**
      发布   ≥85 且无红线 —— auto_publish 直接取。85 是实测天花板 89 往下一档，
             定 90 等于永远不过（历史零篇），这也是 PASS_SCORE 当初定 85 的原因。
      返工   75-84 —— 这是最大的一块（14+ 篇），且**全部无红线**，
             扣分多是「首图不是搜索原句」「开头没关键词」这种改一句的事
             （08-05 那篇 84 分的审核原话就是「改一句即可上 85」）。
             这一档给 loop 自己改，不惊动人 —— 原先它落进「待人工」，
             人不看它就永远停在那，等于把最容易救的一批稿全废了。
      待人工 55-74 —— 短板是结构性的（选题偏、通篇没有原话），改一句救不回来，
             值不值得再投入得由人判断。这一档才配叫「待人工」。
      归档   <55，或红线 + 分数 <70 —— 停手不再消耗。

    红线不再一律归档：红线里像「AI 味强信号≥3 处」是机械可修的，
    分数还在 70 以上就给 loop 一次返工机会。安全性不受影响 ——
    发布仍然要求「≥85 且红线为无」，返工过程中修不掉红线就发不出去。
    """
    clean = (redline or "").strip() in ("", "无", "无。", "None")
    try:
        s = int(str(score).strip())
    except (TypeError, ValueError):
        return "待人工"          # 分数都没解析出来，别替人做决定
    if not clean:
        return "返工" if s >= 70 else "归档"
    if s >= 85:
        return "发布"
    if s >= 75:
        return "返工"
    return "待人工" if s >= 55 else "归档"


LIMIT_WAIT = 30 * 60         # 与 refine_loop 同一策略：撞额度每 30 分钟试一次
LIMIT_MAX_WAIT = 5 * 3600    # 5 小时窗口的额度才值得熬这么久


def run_claude_waiting_out_limits(prompt):
    """撞额度就等，不当失败（2026-08-05 Eric 定）。

    审核和写稿在同一个额度池里，写稿那边熬过了额度、轮到审核又立刻撞上并放弃，
    等于前面白等。所以两边必须用同一套退避策略，否则 loop 仍然会在审核这一步断掉。

    ⛔ 「等」只适用于 session limit（2026-08-11 修）。周额度耗尽要等到重置日，
    熬 5 小时是空转 —— 08-10 审核这一路空转了 32 次。判定逻辑与 refine_loop
    共用 claude_limits，避免两边再次跑偏。
    """
    waited = 0
    while True:
        r = subprocess.run([str(CLAUDE), "-p", prompt],
                           capture_output=True, text=True, timeout=600)
        out = (r.stdout or "").strip()
        kind = classify_limit(out, r.stderr or "")
        if not kind:
            return out
        if kind == WEEKLY:
            print(limit_banner(kind, "审核"), flush=True)
            return ""
        if waited + LIMIT_WAIT > LIMIT_MAX_WAIT:
            print(f"   ⛔ 审核撞额度累计等待 {waited//60} 分钟，超过 {LIMIT_MAX_WAIT//3600} 小时上限，停手")
            return ""
        waited += LIMIT_WAIT
        print(f"   ⏳ 审核撞额度（{kind}），等 {LIMIT_WAIT//60} 分钟后重试"
              f"（已累计 {waited//60}/{LIMIT_MAX_WAIT//60} 分钟）", flush=True)
        time.sleep(LIMIT_WAIT)


def unaudited_drafts():
    """只有「独立审核」行才算审过。

    这里曾是最大的漏洞：任务自评也往 审核记录.csv 写行，一写进去这篇稿就被
    当成已审核而跳过——自评等于抢占了裁判的位置。2026-08-01 那篇自评 87 分判
    「发布」的稿，补做独立审核只有 68 分判「返工」。
    """
    audited = set()
    if AUDIT_LOG.exists():
        for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig")):
            if (row.get("审核方") or "").strip() != "独立审核":
                continue
            audited.add((row.get("成稿文件") or "").strip())
    cutoff = date.today() - timedelta(days=3)
    out = []
    for f in sorted(SUCAI.glob("成稿_*.md")):
        m = DATE_RE.search(f.name)
        if m and date.fromisoformat(m.group(1)) >= cutoff and f.name not in audited:
            out.append(f)
    return out


def mechanical_result(fname):
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "draft_check.py"), "--days", "3"],
        capture_output=True, text=True,
    )
    lines, keep = [], False
    for line in r.stdout.splitlines():
        if line.startswith("⛔"):
            keep = fname in line
        if keep:
            lines.append(line)
    return "\n".join(lines) or "机械检查通过"


def _read_or(path: Path, fallback: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


LANE_HINT = {
    "搜索流": "按 skill 正文的搜索流口径（默认）审核。",
    "推荐流": ("⚠️ 本次按 skill 文末「附录 · 推荐流口径」审核，报告开头须注明「本次按推荐流口径审核」。"
               "差异：标题要留悬念不说答案且张力 6 项命中≥2；首图追求 0.3 秒认知冲突（气质型）而非搜索原句；"
               "开头 15 分（前 3 秒抓手+留悬念）；可信度降为 7 分；主指标看 CES≥8/互动率≥5%；"
               "红线换成「标题把答案说完」，搜索流那三条红线（首图非搜索原句等）本次不适用。"),
}


def audit_one(draft: Path, lane: str = None) -> bool:
    from draft_check import lane_of                    # 同一套口径识别，不重复实现
    text_for_lane = draft.read_text(encoding="utf-8")
    lane = lane or lane_of(text_for_lane)
    skill = (REPO / "skills/eric-xhs-audit/SKILL.md").read_text(encoding="utf-8")
    checklist = (SUCAI / "必须命中清单.md").read_text(encoding="utf-8")
    benchmark_file = SUCAI / "标杆样本库.md"
    benchmark = benchmark_file.read_text(encoding="utf-8") if benchmark_file.exists() else "（标杆样本库缺失）"
    # headless claude 只看得到 prompt 里的东西。skill 写「审核前先读 X」不够，必须喂进来，
    # 否则审核员只能标注「未提供故无法核验」并降级——2026-08-02 连续踩过两次。
    ciku = _read_or(SUCAI / "词库.csv", "（词库缺失）")
    cases = _read_or(SUCAI / "案例库.csv", "（案例库缺失）")
    quotes_lib = _read_or(SUCAI / "评论区原话.csv", "（评论区原话库缺失）")
    # 首图/七卡内容在单独的 cards.json 里。不喂进来，审核员看不到首图，
    # 只能把「首图原句一致性」按未知降级给半分——2026-08-02 三篇稿都栽在这。
    stem = draft.name.removeprefix("成稿_").removesuffix(".md")
    cards = _read_or(SUCAI / f"图文_{stem}_cards.json", "（本稿无卡片 JSON，首图无法核验）")
    text = draft.read_text(encoding="utf-8")
    prompt = f"""你是独立第三方审核员。只依据下面给出的材料审核这篇小红书成稿，不做任何修改建议之外的事。

【本次审核口径：{lane}】
{LANE_HINT[lane]}

【审核标准 skills/eric-xhs-audit】
{skill}

【必须命中清单】
{checklist}

【标杆样本库（⚠️ 推荐流高热样本。搜索流选题不得因「此处无同类先例」扣分）】
{benchmark}

【词库.csv（维度 1 搜索意图匹配的判据：该词竞争密度是否已探测、意图强度）】
{ciku}

【案例库.csv（维度 6 的核对依据：正文引用的原话能否追溯到某个案例 ID）】
{cases}

【评论区原话.csv（维度 6 的另一来源：原话是否照抄不改写）】
{quotes_lib}

【机械检查结果（代码硬核对，以此为准）】
{mechanical_result(draft.name)}

【图文卡片 JSON（维度 3 首图原句一致性的核对依据；第 1 张即首图）】
{cards}

【待审成稿 {draft.name}】
{text}

输出要求（严格遵守）：
第 1 行输出且仅输出一行 CSV（不加代码块），列顺序为：
{MODEL_HEADER}
其中 日期={date.today().isoformat()}，成稿文件={draft.name}，口径填 {lane}，
评级用 绿/黄/橙/红，红线用 无 或简述，
处置列**随便填一个，不必纠结**——这一列会被代码按下面的规则统一改判，你填什么都不影响结果：
  无红线：≥85 发布 / 75-84 返工 / 55-74 待人工 / <55 归档
  有红线：≥70 返工 / <70 归档
你要做的是把**总分和红线判准**，那两列才是真正决定处置的输入。
七个维度列按 skill 的评分卡顺序填分（搜索意图/标题/首图/开头/正文/原话可信度/CTA），
备注以「独立审核」开头并给一句关键结论（备注内不得含逗号，用分号代替）。
第 2 行起输出完整审核报告（7 维逐项+最高优先级改一句）。"""

    out = run_claude_waiting_out_limits(prompt)
    first = next((l for l in out.splitlines() if draft.name in l and l.count(",") >= 14), None)
    if not first:
        print(f"⛔ {draft.name}: 未能解析 CSV 行\n{out[:300]}")
        return False
    fields = next(csv.reader(io.StringIO(first.strip())))
    if len(fields) > 4:
        fields[4] = lane      # 口径由代码填，跟审核方一样不让模型自称
    fields.insert(AUDITOR_COL_INDEX, "独立审核")
    if len(fields) > DISPOSITION_COL_INDEX:
        model_said = fields[DISPOSITION_COL_INDEX].strip()
        decided = decide_disposition(fields[2] if len(fields) > 2 else "",
                                     fields[12] if len(fields) > 12 else "")
        fields[DISPOSITION_COL_INDEX] = decided
        if model_said and model_said != decided:
            print(f"   处置：模型写「{model_said}」→ 按分档规则改判「{decided}」")
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(fields)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(buf.getvalue() + "\n")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"{draft.stem}_独立审核.md").write_text(out, encoding="utf-8")
    print(f"✅ {draft.name} → {first.split(',')[2]} 分（{first.split(',')[3]}）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", metavar="FILENAME",
                    help="强制重审指定成稿（返工后复审用；会追加一行新的独立审核记录）")
    ap.add_argument("--lane", choices=["搜索流", "推荐流"],
                    help="覆盖稿内口径标记（默认读成稿头部的「口径：X」，读不到按搜索流）")
    args = ap.parse_args()

    if args.force:
        target = SUCAI / args.force
        if not target.exists():
            print(f"找不到 {target}", file=sys.stderr)
            return 1
        return 0 if audit_one(target, args.lane) else 1

    drafts = unaudited_drafts()
    if not drafts:
        print("近 3 天成稿均已有审核记录，无需独立审核")
        return 0
    ok = sum(audit_one(d, args.lane) for d in drafts)
    print(f"独立审核完成：{ok}/{len(drafts)}")
    return 0 if ok == len(drafts) else 1


if __name__ == "__main__":
    sys.exit(main())
