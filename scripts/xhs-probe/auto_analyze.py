#!/usr/bin/env python3
"""搜索位空缺探测器 · 第 2 层的 headless 化 —— 把「人工标已验证」这一步去掉。

三层架构原本是：probe.py（自动）→ eric-xhs-probe skill（**要在对话里手动跑**）→ backfill.py（自动）。
中间这一层卡着，所以词库里的「已验证」全靠人手工标，loop 的燃料每天要人喂。
backfill.py 早就有 {"做":"已验证","缓":"候选","放弃":"放弃"} 的映射，缺的只是有人产出
probe_*.result.json —— 这个脚本用独立 claude -p 进程补上，链路自此全自动。

判断权的边界照旧（skill 既有约束）：
  · density.verdict 是 judge_density() 的确定性规则算出来的，模型只能复述不能推翻
  · 模型只判「答案空缺」——这一层没有机械判据，只能靠读懂前排标题和评论
  · 密度高但空缺明显的词仍然可做，不许只看 verdict 就判放弃

用法：
  python3 auto_analyze.py                  # 分析所有缺 .result.json 的探测结果
  python3 auto_analyze.py --date 20260802  # 只分析某天
  python3 auto_analyze.py --limit 3
  python3 auto_analyze.py --list           # 只列出待分析的，不调模型
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_limits import WEEKLY, classify_limit, limit_banner  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
OUT_DIR = SUCAI / "探测原始"
LOG_DIR = SUCAI / "loop日志"
SKILL = REPO / "skills" / "eric-xhs-probe" / "SKILL.md"
CLAUDE = Path.home() / ".local/bin/claude"

TIMEOUT = 600
RETRIES = 1
RETRY_WAIT = 45
LIMIT_WAIT = 30 * 60         # 与写稿/审核同一策略：撞 5 小时窗口额度时每 30 分钟试一次
LIMIT_MAX_WAIT = 5 * 3600    # 周额度不走这条路径（立刻停手），所以上限仍按 5 小时窗口算

# disposition 直接决定词库状态（backfill.DISPOSITION_TO_STATUS），
# 所以判据必须写死在 prompt 里，不能让模型每次自由发挥。
DISPOSITION_RULE = """判定 disposition 的规则（严格执行，不要自由发挥）：
- "做"：能指出**具体某种处境的人没被前排回答**，且该空缺有评论区原话或前排标题作证据。
        密度=高 也可以判"做"——只要空缺具体且有证据（skill 明确要求不得只看密度就放弃）。
- "放弃"：前排已经把这个问题答透了，说不出哪种人没被回答；或空缺只能说成
          「不够深入」「缺实操」这类空话。
- "缓"：数据不完整（completeness != full 或 _error 有值）导致判不了。
        ⛔ 不许因为「拿不准」就填"缓"——拿得准才是你的工作，判不了只有数据缺失这一种理由。"""


def pending(day=None):
    out = []
    pat = f"probe_{day}_*.json" if day else "probe_*.json"
    for p in sorted(OUT_DIR.glob(pat)):
        if p.name.endswith(".result.json"):
            continue
        if not (OUT_DIR / f"{p.stem}.result.json").exists():
            out.append(p)
    return out


def build_prompt(probe_path: Path) -> str:
    raw = probe_path.read_text(encoding="utf-8")
    return f"""你是搜索位空缺分析员。按下面的 skill 分析这份探测数据，只输出 JSON。

【skill: eric-xhs-probe】
{SKILL.read_text(encoding="utf-8")}

【探测数据 {probe_path.name}】
{raw}

{DISPOSITION_RULE}

输出要求（严格遵守）：
只输出一个 JSON 对象，不要代码块、不要任何解释文字。结构：
{{
  "keyword": "<原样复制探测数据里的 keyword>",
  "density_echo": "<原样复制 density.verdict，不得改动>",
  "disposition": "做|缓|放弃",
  "gap": {{
    "existing_answers": ["<前排在给什么答案，带 rank 号，不超过 4 类>"],
    "missing": "<哪种处境的人没被回答，必须具体，禁止「不够深入」这类空话>",
    "evidence": "strong|medium|weak",
    "evidence_quote": "<支持这个判断的一句评论原话或标题，逐字引用>"
  }},
  "title_patterns": [{{"pattern": "<标题句式>", "count": <出现次数>, "saturated": true|false}}],
  "quotes": [
    {{"日期": "{date.today().isoformat()}", "来源链接": "<评论所在笔记 url>",
      "用户原话": "<逐字照抄，不改写>", "暴露的处境": "<这句话暴露了什么处境>",
      "候选词": "<由这句话引出的新长尾词，没有就留空>"}}
  ]
}}"""


def parse_json(out: str):
    """模型偶尔会在 JSON 外面裹解释或代码块，兜住这两种。"""
    out = re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def run_claude(prompt: str, tag: str) -> str:
    """调 headless claude，区分「额度不够」和「答得不对」。

    ⛔ 2026-08-11 之前这里**完全没有额度处理**：撞额度时 claude 打印的是
    "You've hit your weekly limit …"，parse_json 拿不到 JSON，于是被当成
    「分析失败」重试 1 次后丢弃 —— 那条 probe 任务就此消失，且日志里只写
    「第 N 次分析失败」，看不出根因是额度。08-10 这一路 40 次全是这么没的。

    现在与写稿/审核共用 claude_limits 的判定：weekly 立刻停手，
    session/瞬时限流才等，普通失败仍按 RETRIES 重试。
    """
    limit_waited = 0
    attempt = 0
    while True:
        attempt += 1
        try:
            r = subprocess.run([str(CLAUDE), "-p", prompt],
                               capture_output=True, text=True, timeout=TIMEOUT)
            out = (r.stdout or "").strip()
            err = (r.stderr or "")[:2000]
        except subprocess.TimeoutExpired:
            out, err = "", f"超时 {TIMEOUT}s"

        kind = classify_limit(out, err)
        if not kind and out:
            return out

        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (LOG_DIR / f"{date.today().isoformat()}_analyze_{tag}_try{attempt}_{stamp}.txt").write_text(
            f"=== stderr ===\n{err}\n\n=== stdout ===\n{out[:5000]}", encoding="utf-8")

        if kind == WEEKLY:
            print(limit_banner(kind, tag), flush=True)
            return ""
        if kind:
            if limit_waited + LIMIT_WAIT > LIMIT_MAX_WAIT:
                print(f"   ⛔ {tag} 撞额度累计等待 {limit_waited//60} 分钟，超上限，停手")
                return ""
            limit_waited += LIMIT_WAIT
            print(f"   ⏳ {tag} 撞额度（{kind}），等 {LIMIT_WAIT//60} 分钟后重试"
                  f"（已累计 {limit_waited//60}/{LIMIT_MAX_WAIT//60} 分钟）", flush=True)
            time.sleep(LIMIT_WAIT)
            attempt -= 1        # 等额度不算失败，不消耗普通重试次数
            continue

        print(f"   ⚠️ {tag} 第 {attempt} 次分析失败（原始输出已存 loop日志/）")
        if attempt > RETRIES:
            return ""
        time.sleep(RETRY_WAIT)


def analyze(probe_path: Path) -> bool:
    """跑一条 probe 的空缺分析。失败/撞额度都返回 False，原始输出已由 run_claude 留档。"""
    out = run_claude(build_prompt(probe_path), probe_path.stem)
    data = parse_json(out) if out else None
    if not data or data.get("disposition") not in ("做", "缓", "放弃"):
        return False

    src = json.loads(probe_path.read_text(encoding="utf-8"))
    # 复述必须与脚本一致。模型改了就以脚本为准——density 是确定性规则的产物，
    # 让模型的复述覆盖它，等于把判断权交回给模型。
    truth = (src.get("density") or {}).get("verdict", "")
    if truth and data.get("density_echo") != truth:
        print(f"   ⚠️ density_echo「{data.get('density_echo')}」≠ 脚本 verdict「{truth}」，以脚本为准")
        data["density_echo"] = truth
    data["keyword"] = src.get("keyword", data.get("keyword", ""))
    data["_analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    data["_analyzer"] = "auto_analyze.py"
    (OUT_DIR / f"{probe_path.stem}.result.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    gap = (data.get("gap") or {}).get("missing", "")
    print(f"✅ {data['keyword']} → {data['disposition']}（密度 {data['density_echo']}）")
    print(f"   空缺：{gap[:70]}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="只分析某天，格式 YYYYMMDD")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    todo = pending(args.date)
    if not todo:
        print("没有待分析的探测结果（都已有 .result.json）")
        return 0
    if args.list:
        print(f"待分析 {len(todo)} 个：")
        for p in todo:
            print(f"  {p.name}")
        return 0

    todo = todo[:args.limit]
    print(f"待分析 {len(todo)} 个（本次上限 {args.limit}）\n")
    ok = sum(analyze(p) for p in todo)
    print(f"\n分析完成 {ok}/{len(todo)}")
    if ok:
        print("下一步：python3 backfill.py --date <YYYYMMDD> 回填词库（做→已验证）")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
