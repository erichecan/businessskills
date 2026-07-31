#!/usr/bin/env python3
"""独立审核（headless claude）— 裁判与运动员分离。

找出最近 3 天内没有审核记录的 成稿_*.md，用 headless `claude -p` 按
eric-xhs-audit 标准做第三方审核（只给成稿文本，不给写作过程），
把 CSV 行追加进 审核记录.csv，完整报告存 素材库/审核报告/。

由 com.eric.xhsaudit LaunchAgent 每天 09:05 触发；也可手动运行。
"""
import csv
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
HEADER = "日期,成稿文件,总分,评级,选题痛点20,标题20,封面15,开头钩子15,正文网感18,可信度差异7,CTA互动5,红线,处置,备注"


def unaudited_drafts():
    audited = set()
    if AUDIT_LOG.exists():
        for row in csv.DictReader(AUDIT_LOG.open(encoding="utf-8")):
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


def audit_one(draft: Path) -> bool:
    skill = (REPO / "skills/eric-xhs-audit/SKILL.md").read_text(encoding="utf-8")
    checklist = (SUCAI / "必须命中清单.md").read_text(encoding="utf-8")
    text = draft.read_text(encoding="utf-8")
    prompt = f"""你是独立第三方审核员。只依据下面给出的材料审核这篇小红书成稿，不做任何修改建议之外的事。

【审核标准 skills/eric-xhs-audit】
{skill}

【必须命中清单】
{checklist}

【机械检查结果（代码硬核对，以此为准）】
{mechanical_result(draft.name)}

【待审成稿 {draft.name}】
{text}

输出要求（严格遵守）：
第 1 行输出且仅输出一行 CSV（不加代码块），列顺序为：
{HEADER}
其中 日期={date.today().isoformat()}，成稿文件={draft.name}，评级用 绿/黄/橙/红，红线用 无 或简述，处置用 发布/待人工/归档，备注以「独立审核」开头并给一句关键结论（备注内不得含逗号，用分号代替）。
第 2 行起输出完整审核报告（7 维逐项+最高优先级改一句）。"""

    r = subprocess.run([str(CLAUDE), "-p", prompt], capture_output=True, text=True, timeout=600)
    out = (r.stdout or "").strip()
    first = next((l for l in out.splitlines() if draft.name in l and l.count(",") >= 13), None)
    if not first:
        print(f"⛔ {draft.name}: 未能解析 CSV 行\n{out[:300]}")
        return False
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(first.strip() + "\n")
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"{draft.stem}_独立审核.md").write_text(out, encoding="utf-8")
    print(f"✅ {draft.name} → {first.split(',')[2]} 分（{first.split(',')[3]}）")
    return True


def main() -> int:
    drafts = unaudited_drafts()
    if not drafts:
        print("近 3 天成稿均已有审核记录，无需独立审核")
        return 0
    ok = sum(audit_one(d) for d in drafts)
    print(f"独立审核完成：{ok}/{len(drafts)}")
    return 0 if ok == len(drafts) else 1


if __name__ == "__main__":
    sys.exit(main())
