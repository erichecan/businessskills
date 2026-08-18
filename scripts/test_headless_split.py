#!/usr/bin/env python3
"""静态/动态切分的守门测试 —— 不花额度，纯本地。

为什么必须有这个：`--append-system-prompt` 的收益完全建立在
「静态段逐字节稳定」之上。Anthropic 的 prompt cache 按前缀匹配，
**差一个字节，后面整块作废**，而且失败是静默的 —— 缓存没命中不会报错，
只会账单变贵。任何 `date.today()`、随机采样、dict 无序遍历混进静态段，
都会让这套改造悄悄退化回改造前，没人会发现。

改完 build_prompt / build_audit_prompt 一定要跑一次：
    python3 scripts/test_headless_split.py
"""
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "xhs-loop"))
sys.path.insert(0, str(REPO / "scripts" / "xhs-health"))

from headless_cli import SPLIT_MARK, build_argv, split_prompt  # noqa: E402

SUCAI = REPO / "xhs" / "素材库"
ARG_MAX = 1024 * 1024          # macOS getconf ARG_MAX
fails = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '⛔'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


def test_split_basics():
    print("\n[1] split_prompt 基本行为")
    st, dy = split_prompt(f"AAA\n{SPLIT_MARK}以下是本篇专属的材料。\n\nBBB")
    check("有标记时切成两段", st.startswith("AAA") and dy == "BBB", f"static={len(st)} dynamic={dy!r}")
    st2, dy2 = split_prompt("没有标记的 prompt")
    check("无标记时降级为整体动态", st2 == "" and dy2 == "没有标记的 prompt")
    argv = build_argv("/x/claude", "没有标记的 prompt")
    check("降级时不加 --append-system-prompt", "--append-system-prompt" not in argv)


def _statics(prompts):
    return [split_prompt(p)[0] for p in prompts]


def test_write_chain():
    print("\n[2] 写稿链路（refine_loop.build_prompt）")
    import refine_loop as RL
    row = RL.pick_topic()
    if not row:
        check("取到选题", False, "pick_topic() 返回空，跳过本组")
        return
    variants = {
        "原选题": RL.build_prompt(dict(row), "", 1, "搜索流", ""),
        "换关键词": RL.build_prompt({**row, "关键词": "试用期没拿到结果怎么办"}, "", 1, "搜索流", ""),
        "换场景域": RL.build_prompt({**row, "关键词": "晋升答辩被问倒", "场景域": "面试"}, "", 1, "搜索流", ""),
        "返工轮": RL.build_prompt(dict(row), "【扣分点】开头三句复述同一论点", 2, "搜索流", ""),
        "指定标题": RL.build_prompt(dict(row), "", 1, "搜索流", "面试官其实在筛这个"),
    }
    sts = _statics(variants.values())
    check("每个变体都能切出静态段", all(sts), f"长度 {[len(s) for s in sts]}")
    check("五个变体静态段逐字节一致", len(set(sts)) == 1, f"{len(sts[0]):,} 字")
    _assert_stable(sts[0], "写稿")
    _assert_argv(RL.CLAUDE if hasattr(RL, "CLAUDE") else "/x/claude", variants["原选题"], "写稿")
    # 推荐流是另一套前缀，这是预期的（LANE_SPEC.rules 不同），只确认它也能切
    st_rec = split_prompt(RL.build_prompt(dict(row), "", 1, "推荐流", ""))[0]
    check("推荐流自成一套前缀（预期）", bool(st_rec) and st_rec != sts[0], f"{len(st_rec):,} 字")


def test_audit_chain():
    print("\n[3] 审核链路（independent_audit.build_audit_prompt）")
    import independent_audit as IA
    drafts = sorted(SUCAI.glob("成稿_*.md"), reverse=True)[:3]
    if len(drafts) < 2:
        check("素材库里有 ≥2 篇成稿", False, "跳过本组")
        return
    prompts = [IA.build_audit_prompt(d, None)[0] for d in drafts]
    sts = _statics(prompts)
    check("每篇都能切出静态段", all(sts), f"长度 {[len(s) for s in sts]}")
    check(f"{len(drafts)} 篇静态段逐字节一致", len(set(sts)) == 1, f"{len(sts[0]):,} 字")
    check("动态段以【词库.csv 开头", all(split_prompt(p)[1].startswith("【词库.csv") for p in prompts))
    _assert_stable(sts[0], "审核")
    _assert_argv(IA.CLAUDE, prompts[0], "审核")


def _assert_stable(static, tag):
    """静态段里不能出现今天的日期 —— 那是最容易混进来的隐形失效源。"""
    today = date.today().isoformat()
    check(f"{tag}静态段不含今天日期（{today}）", today not in static)
    check(f"{tag}静态段含切分标记", SPLIT_MARK in static)


def _assert_argv(claude_bin, prompt, tag):
    argv = build_argv(claude_bin, prompt)
    nbytes = sum(len(a.encode()) for a in argv)
    check(f"{tag} argv 在 ARG_MAX 内", nbytes < ARG_MAX * 0.5,
          f"{nbytes / 1024:.0f} KB / 上限 {ARG_MAX // 1024} KB")
    check(f"{tag} argv 带 --append-system-prompt", "--append-system-prompt" in argv)
    check(f"{tag} argv 以 -p <动态段> 结尾", argv[-2] == "-p" and argv[-1].startswith("【") or argv[-2] == "-p")


if __name__ == "__main__":
    test_split_basics()
    test_write_chain()
    test_audit_chain()
    print(f"\n{'⛔ 失败 ' + str(len(fails)) + ' 项：' + '、'.join(fails) if fails else '✅ 全部通过'}")
    sys.exit(1 if fails else 0)
