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
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
AUDIT_LOG = SUCAI / "审核记录.csv"
REPORT_DIR = SUCAI / "审核报告"
CLAUDE = Path.home() / ".local/bin/claude"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 给模型的列（不含审核方——那一列由代码填，模型无权自称是谁）
# 维度列不带分值——口径切换时满分会变（搜索流 20/20/15/10/15/15/5，推荐流 20/20/15/15/18/7/5）
MODEL_HEADER = ("日期,成稿文件,总分,评级,口径,选题,标题,首图,开头,正文,"
                "可信度,CTA,红线,处置,备注")
AUDITOR_COL_INDEX = 13  # 红线之后、处置之前
HEADER = MODEL_HEADER.replace("红线,处置", "红线,审核方,处置")


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
评级用 绿/黄/橙/红，红线用 无 或简述，处置用 发布/待人工/归档，
七个维度列按 skill 的评分卡顺序填分（搜索意图/标题/首图/开头/正文/原话可信度/CTA），
备注以「独立审核」开头并给一句关键结论（备注内不得含逗号，用分号代替）。
第 2 行起输出完整审核报告（7 维逐项+最高优先级改一句）。"""

    r = subprocess.run([str(CLAUDE), "-p", prompt], capture_output=True, text=True, timeout=600)
    out = (r.stdout or "").strip()
    first = next((l for l in out.splitlines() if draft.name in l and l.count(",") >= 14), None)
    if not first:
        print(f"⛔ {draft.name}: 未能解析 CSV 行\n{out[:300]}")
        return False
    fields = next(csv.reader(io.StringIO(first.strip())))
    if len(fields) > 4:
        fields[4] = lane      # 口径由代码填，跟审核方一样不让模型自称
    fields.insert(AUDITOR_COL_INDEX, "独立审核")
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
