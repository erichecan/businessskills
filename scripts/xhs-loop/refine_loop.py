#!/usr/bin/env python3
"""成稿质量闭环 — 选题→成稿→机械检查→独立审核→返工，直到过线或到轮次上限。

为什么要这个脚本：8:30 那个 scheduled task 自称「打分循环至90分」，但打分的是写稿的
那个模型自己。独立审核回来的真实分是 72–86，11 篇只有 1 篇过闸门。自评 87 / 独立审核 67
的那次差距就是 D7 决策的由来。所以这里把评分权从写稿的模型手里拿走：

  ① 每轮返工都是**新的 claude -p 进程** —— 同一会话里自己改自己评，改完必然自评合格
  ② 机械检查用 draft_check.py（代码硬核对），模型自报无效
  ③ 打分用 independent_audit.py（只喂成稿文本，不喂写作过程）
  ④ 过线阈值 85 而不是 90 —— 90 是自评口径的数字，独立审核口径下 21 篇历史稿最高 86，
     定 90 等于永远不过，loop 会空转到上限

用法：
  python3 refine_loop.py                      # 自动选一个已验证关键词跑完整 loop
  python3 refine_loop.py --topic "关键词"      # 指定选题
  python3 refine_loop.py --dry-run            # 只看选中哪个词、喂什么料，不调 claude
  python3 refine_loop.py --rounds 2           # 改轮次上限（默认 3）
"""
import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUCAI = REPO / "xhs" / "素材库"
CIKU = SUCAI / "词库.csv"
AUDIT_LOG = SUCAI / "审核记录.csv"
PENDING = SUCAI / "待激活素材库.csv"
PROBE_DIR = SUCAI / "探测原始"
CARDS_SCRIPT = SUCAI / "图文模板" / "make_cards.py"
HEALTH = REPO / "scripts" / "xhs-health"
CLAUDE = Path.home() / ".local/bin/claude"

PASS_SCORE = 85
MAX_ROUNDS = 3
WRITE_TIMEOUT = 900
RETRIES = 2          # claude -p 调用失败重试次数
RETRY_WAIT = 45      # 重试前等待秒数（失败多半是限流，立刻重试没意义）
LOG_DIR = REPO / "xhs" / "素材库" / "loop日志"

SEP_SLUG, SEP_MD, SEP_JSON, SEP_END = "===SLUG===", "===MARKDOWN===", "===CARDS_JSON===", "===END==="

# 返工时不提醒的话，模型会为了修一条意见把已经达标的机械项改坏（实测：第 3 轮为了改
# 审核意见，具体名词密度从 2.0+ 掉到 1.51，连机械检查都没过，比上一轮还差）。
KEEP_PASSED = """⚠️ 改的是被指出的问题，别把已经达标的地方改坏。尤其守住这几条：
正文字数守住本篇口径的规格 · 平均句长≤30 · 引语密度≥0.8/百字 ·
具体名词（数字/时间/金额/职位）≥2.0/百字 · 排比≤1 处 · 「不是X是Y」≤2 处 ·
结尾保留具体问句。改完自己数一遍再输出。"""

# 两条流**共用一套流程和一个正文规格**，只在标题/首图/开头三处切口径。
# Eric 2026-08-03 两条决定：①推荐流不做视频、按同一条成稿路线做图文，账号丰富度才起得来
# ②推荐流正文也写 300-500（原来 800-1200）—— 字数再分叉，流程就变成两套了。
# 口径差异的依据是 eric-xhs-audit SKILL.md 文末「附录 · 推荐流口径」。
LANE_SPEC = {
    "搜索流": {
        "words": "300-500",
        "brief": "搜索流图文笔记（7 张图承载内容 + 300-500 字正文承载关键词）",
        "rules": """- 标题：直接给搜索原句，关键词放最左，**不要留悬念**，≤20 字
- 首图：搜索原句大字 + 结论前置（信息型），让搜索的人一眼确认「答的就是我搜的」
- 开头：10 分档，让搜索者确认答的就是他搜的，不要铺垫
- 可信度是最重项（15 分）：原话颗粒度决定他信不信你
- ⛔ 红线：首图不是搜索原句 / 出现「表达力」三字 / 编造原话""",
    },
    "推荐流": {
        "words": "300-500",
        "brief": "推荐流图文笔记（7 张图承载内容 + 300-500 字正文）",
        "rules": """- 标题：**留悬念不把答案说完**，张力 6 项（对比/数字/悬念/冲突/时间承诺/结果承诺）命中 ≥2，≤20 字
- 首图：0.3 秒制造认知冲突/好奇/焦虑（气质型），**不要**搜索原句大字
- 开头：15 分档，前 3 秒抓手 + 留悬念
- 主指标是 CES（赞×1+藏×1+评×4+转×4）和互动率 ≥5%，所以评论钩子权重更高
- ⛔ 红线：标题把答案说完 = 必死（这条与搜索流正好相反）
- ⚠️ 推荐流暂无高分范例可参照（历史推荐流稿最高 69 分），下面给的范例是搜索流的，
  只学它的结构组织和原话使用方式，标题/首图/开头一律按上面推荐流口径重做""",
    },
}


def _read_or(path: Path, fallback: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def used_keywords() -> set:
    """已经成过稿的关键词。

    不另建索引表——成稿 md 的头部会写「关键词来源：词库.csv「XXX」」，
    直接全文匹配即可，少一张要维护的表就少一处会对不上的地方。
    """
    files = list(SUCAI.glob("成稿_*.md")) + list((SUCAI / "归档稿").glob("成稿_*.md"))
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return {kw for kw in _all_keywords() if kw and kw in blob}


def _all_keywords() -> list:
    if not CIKU.exists():
        return []
    return [(r.get("关键词") or "").strip() for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))]


def pick_topic() -> dict | None:
    """选一个搜索位已验证、且没写过的词，密度已探测的优先。

    状态=已验证 是硬条件——它代表人工核过这个词的搜索位确实有空缺，没验过就写容易撞饱和赛道。
    密度「待探测」不是硬条件：必须命中清单第 8 条允许「两者皆无时在备注说明为什么值得赌」，
    只排后面。初版把它也当硬条件，结果 9 个已验证词里 7 个已写过、剩 2 个全是待探测，
    loop 直接无题可做 —— 比清单本身还严，把自己饿死了。
    """
    if not CIKU.exists():
        return None
    used = used_keywords()
    pool = [r for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))
            if (r.get("关键词") or "").strip()
            and (r.get("关键词") or "").strip() not in used
            and (r.get("状态") or "").strip() == "已验证"]
    pool.sort(key=lambda r: (r.get("竞争密度") or "").strip() in ("", "待探测"))
    return pool[0] if pool else None


def probe_evidence(keyword: str) -> str:
    """该词的探测原始数据。审核维度 1 要核competition密度是否有 probe 文件支撑。"""
    hits = []
    for f in sorted(PROBE_DIR.glob("probe_*.json")) if PROBE_DIR.exists() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if keyword in json.dumps(data, ensure_ascii=False):
            hits.append(f"【{f.name}】\n{json.dumps(data, ensure_ascii=False, indent=1)[:3000]}")
    return "\n\n".join(hits) or "（该词无 probe_*.json，需在备注说明为什么值得赌）"


def pose_library() -> str:
    """姿势库清单，读目录不写死 —— make_cards.py 的注释就因为写死而停在「共 18 个」，
    实际已经 30 个了。文件名自带语义（pose19_谈薪拉扯），模型据此选图。"""
    d = SUCAI / "图文模板"
    names = sorted((p.stem for p in d.glob("pose*.png")),
                   key=lambda s: int(re.match(r"pose(\d+)", s).group(1)))
    return "、".join(names)


def recent_drafts(n=5) -> str:
    """近 n 篇成稿全文——给模型看「别再用这些开头和签名句」。

    跨篇查重是机械检查项（清单第 5 条），事后拦不如事前给。
    """
    files = sorted(SUCAI.glob("成稿_*.md"))[-n:]
    return "\n\n".join(f"【{f.name}】\n{f.read_text(encoding='utf-8')[:1500]}" for f in files)


def build_prompt(row: dict, feedback: str, round_no: int, lane: str = "搜索流") -> str:
    kw = row["关键词"]
    spec = LANE_SPEC[lane]
    benchmark = _read_or(SUCAI / "成稿_2026-08-02_空降前30天.md", "（无范例）")
    rework = ""
    if feedback:
        rework = f"""
⛔ 这是第 {round_no} 轮返工，不是新写。上一轮的稿被打回，原因如下。
逐条修掉，不要重写无关的部分，也不要为了绕开某条检查而牺牲可读性：

{feedback}
"""
    return f"""你是 Eric 的小红书成稿写手。写一篇{spec['brief']}。
{rework}
【本篇口径：{lane}】必须在成稿头部的引言区写一行 `> 口径：{lane}` ——
draft_check.py 和 independent_audit.py 都靠这一行判断用哪套规格，漏了会按搜索流误判。
{spec['rules']}

【今天是 {date.today().isoformat()}】成稿将命名为 成稿_{date.today().isoformat()}_<你给的短名>.md，
文中提到日期或引用卡片文件名时按这个日期写。

【选题】关键词：{kw}
场景域 {row.get('场景域','')} · 意图强度 {row.get('意图强度','')} · 竞争密度 {row.get('竞争密度','')} · 关联案例 {row.get('关联案例ID','')}

【必须命中清单（逐条命中，机械项由代码核对，自报无效）】
{_read_or(SUCAI / '必须命中清单.md', '（清单缺失）')}

【案例库.csv（正文引用的原话必须能追溯到这里的某一行；注意「来源」列决定人称）】
{_read_or(SUCAI / '案例库.csv', '（案例库缺失）')}

【评论区原话.csv（另一个合法原话来源，逐字引用不改写）】
{_read_or(SUCAI / '评论区原话.csv', '（原话库缺失）')}

【该词的探测数据】
{probe_evidence(kw)}

【格式范例（这篇独立审核 86 分，是目前唯一过闸门的稿。学它的结构，别抄它的内容）】
⚠️ 它自己被扣分的两处别学：①「上面七张图讲的是…」这类元叙述——正文不要提"图里讲了什么"，
图文各说各的；②末段密集堆评论原话，把落点冲淡了。
{benchmark}

【近 5 篇成稿（开头钩子和签名句不得与这些重复）】
{recent_drafts()}

⛔ 真实性红线：正文里的每一句引语都必须逐字来自上面的案例库或评论区原话库。
不许编造对话、不许编造数字。案例库「来源」列是「采集」的，不能写成"我"的亲身经历，
要写成"有人在评论区说""看到有人分享"；只有「来源=自有」的才能用第一人称。

输出格式（严格遵守，不要输出任何其他内容，不要用代码块包裹）：
{SEP_SLUG}
<文件名短名，6-10 个汉字，不含标点，例如：空降前30天>
{SEP_MD}
<成稿 markdown 全文，章节顺序照范例：标题 / 发布标题 / 正文 / 图文卡片 / 话题标签 / 处置>
{SEP_JSON}
<7 个卡片对象的 JSON 数组，字段 type/tag/title/quote/body/pose，
 type 依次为 cover,scene,contrast,quote,why,formula,boundary，body 内换行用 <br>>
{SEP_END}

【pose 字段 —— 每张卡必须给，按这张卡讲的内容选人物姿势】
可选姿势（文件名自带语义，照抄名字，不要加 .png）：
{pose_library()}

选图规则：
- 按**这张卡的内容**选，不是按卡型选。讲谈薪拉扯就用 pose19_谈薪拉扯，
  讲被追问答不上来就用 pose8_被追问冒汗，讲复盘记录就用 pose30_记三行笔记。
- ⛔ 7 张卡不许出现重复姿势 —— 每篇笔记的图现在长得都一样，就是因为以前按卡型
  固定映射（scene 永远是面试对坐、contrast 永远是摊手），30 个姿势只用到 9 个。
- 实在没有贴切的，选情绪对得上的（叹气/沉思/如释重负），别硬凑动作。"""


def parse_output(out: str):
    def between(a, b):
        m = re.search(re.escape(a) + r"\s*(.*?)\s*" + re.escape(b), out, re.S)
        return m.group(1).strip() if m else ""
    slug = between(SEP_SLUG, SEP_MD)
    md = between(SEP_MD, SEP_JSON)
    raw = between(SEP_JSON, SEP_END) or out.split(SEP_JSON)[-1].strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        cards = json.loads(raw)
    except json.JSONDecodeError:
        cards = None
    slug = re.sub(r"[^\w一-鿿]", "", slug)[:12]
    return slug, md, cards


def run_claude(prompt: str, tag: str) -> str:
    """调 headless claude，失败重试并把原始输出留档。

    一轮 loop 最多要调 6 次 claude -p，每次 60k+ token，连续调用撞限流是常态。
    首跑实测：第 2 轮审核和第 3 轮成稿接连返回异常输出，第一次失败就放弃，
    等于把前面两轮的工都扔了。失败时把 stdout/stderr 存进 loop日志/ ——
    不然只看到「解析失败」四个字，没法判断是限流、超时还是格式跑偏。
    """
    for attempt in range(1, RETRIES + 2):
        try:
            r = subprocess.run([str(CLAUDE), "-p", prompt],
                               capture_output=True, text=True, timeout=WRITE_TIMEOUT)
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out:
                return out
            err = (r.stderr or "")[:2000]
        except subprocess.TimeoutExpired:
            out, err = "", f"超时 {WRITE_TIMEOUT}s"
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (LOG_DIR / f"{date.today().isoformat()}_{tag}_try{attempt}_{stamp}.txt").write_text(
            f"=== stderr ===\n{err}\n\n=== stdout ===\n{out[:5000]}", encoding="utf-8")
        print(f"   ⚠️ {tag} 第 {attempt} 次调用失败（原始输出已存 loop日志/）")
        if attempt <= RETRIES:
            time.sleep(RETRY_WAIT * attempt)
    return ""


def write_draft(row, feedback, round_no, dry_run=False, lane="搜索流"):
    prompt = build_prompt(row, feedback, round_no, lane)
    if dry_run:
        print(f"--- [dry-run] 第 {round_no} 轮 prompt 共 {len(prompt)} 字，前 600 字：\n{prompt[:600]}\n---")
        return None, None, None
    return parse_output(run_claude(prompt, f"write_r{round_no}"))


def save(slug, md, cards):
    today = date.today().isoformat()
    stem = f"{today}_{slug}"
    # headless claude 不知道今天几号，md 正文里那句「卡片见 图文_YYYY-MM-DD_xxx_cards.json」
    # 常常写成别的日期（跨午夜跑必错），指向一个不存在的文件。按实际落盘名校正。
    md = re.sub(r"图文_\d{4}-\d{2}-\d{2}_[^\s`）)]*?_cards\.json", f"图文_{stem}_cards.json", md)
    draft = SUCAI / f"成稿_{stem}.md"
    draft.write_text(md, encoding="utf-8")
    if cards:
        (SUCAI / f"图文_{stem}_cards.json").write_text(
            json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    return draft


def render_cards(slug: str) -> bool:
    """渲染 7 张卡片图（headless Chrome，零 AI 生图成本）。

    发布闸门第 4 条要求成品图目录有已渲染的图，不渲染的话稿子再好也卡在闸门外——
    实测那篇 89 分绿稿就是因为这个被判 ⛔，链路差这一步没通。
    """
    today = date.today().isoformat()
    src = SUCAI / f"图文_{today}_{slug}_cards.json"
    out = SUCAI / "成品图" / f"{today}_{slug}"
    if not src.exists():
        print("   ⚠️ 无卡片 JSON，跳过渲染（发布闸门会拦下）")
        return False
    r = subprocess.run([sys.executable, str(CARDS_SCRIPT), str(src), f"{out}/"],
                       cwd=CARDS_SCRIPT.parent, capture_output=True, text=True, timeout=300)
    n = len(list(out.glob("*.png"))) if out.exists() else 0
    if r.returncode != 0 or n < 7:
        print(f"   ⚠️ 卡片渲染异常（{n}/7 张）：{(r.stderr or r.stdout)[:200]}")
        return False
    print(f"   卡片图 → 成品图/{today}_{slug}/（{n} 张）")
    return True


def mech_check(fname: str, lane="搜索流"):
    r = subprocess.run([sys.executable, str(HEALTH / "draft_check.py"), "--file", fname,
                        "--lane", lane], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or "").strip()


def _audit_rows(fname: str) -> list:
    if not AUDIT_LOG.exists():
        return []
    return [r for r in csv.DictReader(AUDIT_LOG.open(encoding="utf-8-sig"))
            if (r.get("成稿文件") or "").strip() == fname
            and (r.get("审核方") or "").strip() == "独立审核"]


def audit(fname: str, lane="搜索流"):
    """跑独立审核并取本轮新增的那一行。分数为 None = 审核失败（不是低分）。

    ⛔ 这里曾经只取「该文件最后一行独立审核记录」，不管这轮审核有没有真的写出新行。
    首跑就踩了：第 2 轮审核调用失败没写行，函数原样返回上一轮的 83 分，loop 以为
    返工没效果 —— 实际那一稿手动复审是 89 分，早该过线收工。更危险的方向是反过来：
    上一轮若是 90 分，这次审核失败会被读成「过线」直接放行一篇没审过的稿。
    所以改成用行数增量判断，审出来才算数。
    """
    before = len(_audit_rows(fname))
    for attempt in (1, 2):
        r = subprocess.run([sys.executable, str(HEALTH / "independent_audit.py"), "--force", fname,
                            "--lane", lane], capture_output=True, text=True, timeout=WRITE_TIMEOUT)
        rows = _audit_rows(fname)
        if len(rows) > before:
            break
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (LOG_DIR / f"{date.today().isoformat()}_audit_try{attempt}_{stamp}.txt").write_text(
            f"=== stdout ===\n{r.stdout[:5000]}\n\n=== stderr ===\n{r.stderr[:2000]}", encoding="utf-8")
        print(f"   ⚠️ 独立审核第 {attempt} 次未写出记录（原始输出已存 loop日志/）")
        if attempt == 1:
            time.sleep(RETRY_WAIT)
    else:
        return None, "审核未产生新记录", ""

    last = rows[-1]
    try:
        score = int((last.get("总分") or "0").strip())
    except ValueError:
        score = 0
    report = _read_or(SUCAI / "审核报告" / f"{Path(fname).stem}_独立审核.md", last.get("备注", ""))
    return score, (last.get("红线") or "").strip(), report


def park(draft: Path, score, note):
    """3 轮仍不过 → 移进 归档稿/ 并登记待激活库，停手不再消耗。

    这个止损机制不是新发明：待激活素材库.csv 里那条 86 分的稿就是「3 轮定向修改后仍卡在
    上限」被存起来的，已经验证过有效。按既有约定要真的把文件移进 归档稿/——
    留在素材库根目录的话，health_check 每天都会拿它报一次机械检查违规，
    而它已经是「决定不发」的稿，那个告警纯属噪音。
    """
    archive = SUCAI / "归档稿"
    archive.mkdir(exist_ok=True)
    target = archive / draft.name
    draft.replace(target)
    new = not PENDING.exists()
    with PENDING.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日期", "选题名", "最终得分", "主要扣分项", "成稿文件路径", "状态"])
        w.writerow([date.today().isoformat(), draft.stem, score, note[:300],
                    str(target.relative_to(SUCAI)), "待激活"])
    print(f"   已移入 归档稿/{draft.name}")


def predict_one(draft_name: str, lane: str):
    """过线后押个数，7 天后由 review_prediction.py 对账。

    不押数就没法复盘：「这篇效果不好」是感觉，「预测观看 400 实际 120」才是能改进的结论。
    """
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "predict.py"),
                        draft_name, "--lane", lane], capture_output=True, text=True, timeout=120)
    print((r.stdout or "").strip() or f"   ⚠️ 预测失败：{(r.stderr or '')[:200]}")


def run_one(row, args, lane) -> str:
    """跑完一篇的完整闭环。返回 过线 / 归档 / 失败。"""
    kw = row["关键词"]
    print(f"\n{'='*54}\n选题：{kw}（{lane} · 密度 {row.get('竞争密度','?')} · 意图 {row.get('意图强度','?')}）")

    # 越改越差是真会发生的：复跑实测第 2 轮 84 分，第 3 轮为了修审核意见把具体名词密度
    # 从 2.0+ 改到 1.51，连机械检查都没过。只留最后一版等于把最好的那版扔了，所以择优。
    best = {"score": -1, "md": None, "cards": None}
    feedback, draft, score, slug, md = "", None, 0, None, None
    for rnd in range(1, args.rounds + 1):
        print(f"\n--- 第 {rnd}/{args.rounds} 轮 ---")
        new_slug, md, cards = write_draft(row, feedback, rnd, lane=lane)
        if not new_slug or not md:
            print("⛔ 成稿输出解析失败（原始输出见 loop日志/）")
            break
        # 短名固定用第一轮的：模型每轮可能起不同短名，不固定就会散落好几个文件，
        # 而检查和审核只认最后一个，前面几份成了没人管的孤儿稿。
        slug = slug or new_slug
        draft = save(slug, md, cards)
        print(f"成稿 → {draft.name}" + ("" if cards else "（⚠️ 卡片 JSON 解析失败，首图无法核验）"))

        ok, mech = mech_check(draft.name, lane)
        if not ok:
            print(f"机械检查未过：\n{mech}")
            feedback = f"【机械检查（代码硬核对，必须全部修掉）】\n{mech}\n{KEEP_PASSED}"
            continue
        print("机械检查通过 → 送独立审核")

        score, redline, report = audit(draft.name, lane)
        if score is None:
            print("⛔ 独立审核连续两次未写出记录，本轮无法判定，停手（不拿上一轮旧分充数）")
            break
        print(f"独立审核：{score} 分 · 红线：{redline or '无'}")
        if score > best["score"]:
            best = {"score": score, "md": md, "cards": cards}
        if score >= args.threshold and redline in ("", "无"):
            print(f"\n✅ 过线（≥{args.threshold} 且无红线）")
            render_cards(slug)
            predict_one(draft.name, lane)
            print("   → 交给 auto_publish 闸门（次日 10:00 定时任务会取）")
            return "过线"
        feedback = (f"【独立审核 {score} 分，未过线（需 ≥{args.threshold} 且红线为无）】\n"
                    f"{report[:4000]}\n{KEEP_PASSED}")

    if not draft:
        print("⛔ 一轮成稿都没产出，无产物可归档")
        return "失败"
    # 比内容不比分数：机械检查未过的轮次根本没拿到分，score 还留着上一轮的值，
    # 拿它比较会得出「最后一版就是最佳」的错误结论，回滚静默失效。
    if best["md"] and best["md"] != md:
        draft = save(slug, best["md"], best["cards"])
        print(f"回滚到最佳版本（{best['score']} 分，最后一版更差）")
    print("⏸ 未过线／中途停手，存入待激活素材库")
    park(draft, max(best["score"], 0), feedback or "loop 中断，未取得有效审核分")
    return "归档"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="指定关键词（默认从词库自动选；--count>1 时只作用于第一篇）")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--threshold", type=int, default=PASS_SCORE)
    ap.add_argument("--count", type=int, default=1, help="连跑几篇（消化候选积压用）")
    ap.add_argument("--lane", default="搜索流", choices=["搜索流", "推荐流"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tally = {"过线": 0, "归档": 0, "失败": 0}
    for i in range(args.count):
        if args.topic and i == 0:
            rows = [r for r in csv.DictReader(CIKU.open(encoding="utf-8-sig"))
                    if (r.get("关键词") or "").strip() == args.topic]
            row = rows[0] if rows else {"关键词": args.topic}
        else:
            row = pick_topic()
        if not row:
            # 燃料耗尽不是「今天没事干」，是上游断供了：采集 → probe → auto_analyze。
            # 这条链停在哪一环，loop 就从哪一天起空转，说清楚该去补哪一环。
            print("⛔ 无题可做：词库里没有「状态=已验证 且 未写过」的词。")
            print("   燃料在上游：python3 scripts/xhs-probe/probe.py --from-cikuku --limit 5")
            print("             → auto_analyze.py → backfill.py（做→已验证，全自动）")
            return 1 if i == 0 else 0

        if args.dry_run:
            print(f"选题：{row['关键词']}（{args.lane}）")
            write_draft(row, "", 1, dry_run=True, lane=args.lane)
            return 0
        tally[run_one(row, args, args.lane)] += 1

    if args.count > 1:
        print(f"\n{'='*54}\n本轮 {args.count} 篇："
              f"过线 {tally['过线']} · 归档 {tally['归档']} · 失败 {tally['失败']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
