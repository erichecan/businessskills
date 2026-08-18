#!/usr/bin/env python3
"""headless `claude -p` 的调用参数 —— 写稿/审核/probe/采集/分诊共用。

## 一、固定开销：38.7k 不是地板价，14.8k 才是（2026-08-18 更正）

2026-08-11 实测，一句 5 个字的 prompt 在项目目录下用默认参数调用，输入侧
46,091 token。这不是我们的内容，是 CLI 每次注入的：CLAUDE.md（全局 + 项目）、
全套工具定义、skills 列表、system prompt。

当时的结论是「38,704 是 CLI 路径下的地板价」，理由是 `--bare` 虽然能连全局
CLAUDE.md 一起跳掉，但它同时跳过 keychain 读取，直接 "Not logged in"，
订阅登录态拿不到。**这个结论错在漏了 `--safe-mode`** —— 它同样禁掉 CLAUDE.md /
skills / plugins / hooks / MCP，但 `--help` 明确写着 auth、model selection、
built-in tools、permissions 照常工作。实测（空 cwd + 禁全部工具，同一句 5 字 prompt）：

    现状（空 cwd + 禁工具）                  38,811 token   新写 1h 缓存 26,106   $0.268
    + --safe-mode                            17,487 token   新写 1h 缓存  4,782   $0.054
    + --safe-mode --disable-slash-commands   14,771 token   新写 1h 缓存  2,689   $0.033

**每次省 23.4k 新写缓存 = $0.234。**

⛔ 不要用 `--system-prompt` 替换默认 system prompt。实测 14,819 token（比上面还少 5%），
但成本 $0.148 —— 涨了 4.5 倍。因为默认 system prompt 里那 12k 是**跨会话共享的
缓存读**（0.1×），换成自己的就变成每次全新**写入**（2×）。要加东西只能用
`--append-system-prompt`（追加，不替换）。

## 二、用户 prompt 不参与 prompt caching —— 所以静态区必须挪到 system prompt

2026-08-18 决定性实验：同一段 prompt 一字不差连跑三次，

    ① 静态+问题A   写 1h 缓存 75.5k   读缓存 15.7k
    ② 静态+问题B   写 1h 缓存 76.9k   读缓存 15.7k
    ③ 和 ② 完全相同，重跑        76.1k        15.7k   ← 一分没少

生产数据同样：8/11 那批连跑 13 篇（间隔 2.5–5 分钟），读缓存**恒定 15.6k**。
那 15.6k 是 CLI 自己的 system prompt；**用户 prompt 无论重复多少次都是全新写入、
永不读回**。cache_control 的断点打在 system prompt 结束处，用户消息整体不参与前缀匹配。

⛔ 由此作废一条旧结论：`build_prompt` 里 2026-08-12 那次「静态在前、动态在后」的
前缀重排**在 CLI 路径下省不到钱**。共同前缀确实从 5.5% 提到 66.7%，但字符串对齐
不等于缓存边界对齐。**别再用「共同前缀字符数」推断缓存命中，要看
`cache_read_input_tokens` 有没有真的涨。**

修法是换位置：把每篇都一样的那段挪进 `--append-system-prompt`。同一段静态内容：

    全塞用户 prompt          第 1 次 $0.798   第 2 次起 $0.798（无衰减）
    + append-system-prompt   第 1 次 $0.798   第 2 次起 $0.274
    + safe-mode 一起用       第 1 次 $0.571   第 2 次起 $0.046

真实审核 prompt 上验证过：换一篇完全不同的稿，静态前缀照样命中 39.7k 读缓存，
单次 $0.879 → $0.285。**跨稿件复用成立，不是只有同一篇重跑才管用。**

⚠️ TTL 是 1 小时，所以**批次要集中**：每批第一篇付全价，第 2 篇起才命中。
宁可一次多跑几篇，不要把篇数摊到更多触发点上。

⚠️ 静态段必须**逐字节稳定**。任何 `date.today()`、随机采样、dict 无序遍历
混进去，整个前缀就废了。改完务必跑一次 `python3 scripts/test_headless_split.py`。

## 三、真正的大头不是这点固定开销，是消掉多轮工具往返

写稿会话曾实测 24.4 次请求 / 14.1 次 Bash，而 999 次 Bash 命令里排前面的是：

    85×  cd .../scripts/xhs-health          31×  ls .../scripts/xhs-health
    25×  ls .../xhs/素材库/ | head            7×  head -60 draft_check.py

模型不是在跑机械检查（那早就由脚本在会话外跑，见 refine_loop 第 578 行），
而是在**找路**。诱因是 prompt 里那句「改完自己数一遍」—— 它真的去找工具来数。
prompt 是自包含的，成稿文件由脚本 save() 写，模型不需要任何工具。禁掉即可。
"""
from pathlib import Path

# 工具名必须是 CLI 认得的，写错会直接报 "matches no known tool" 并拒跑。
# ⛔ 必须用逗号分隔成**一个**参数：--disallowedTools 是变长参数（<tools...>），
# 空格分隔会把后面的 prompt 也吞进去当工具名。
_DISALLOWED = ",".join([
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit",
])

HEADLESS_FLAGS = [
    "--disallowedTools", _DISALLOWED,
    # 见上方第一节：禁 CLAUDE.md / skills / plugins / hooks / MCP，保留 auth。
    "--safe-mode",
    "--disable-slash-commands",
    # effort medium：审核 A/B 实测 87 分（基线 high 是 86），六维分只差 1，
    # 15 字段格式无损，输出 −26%、耗时 −26%。
    # ⛔ 这一档只在 Opus 上安全：同样 medium，Sonnet 会把 CSV 多插一列且不报错，
    #    还能通过「逗号 ≥14」的解析判据，静默把错值写进审核记录。别顺手换模型。
    "--effort", "medium",
]

# 静态/动态切分标记。写稿 prompt 里本来就有这一行（2026-08-12 前缀重排时加的），
# 审核 prompt 由 build_audit_prompt 插入同一行。没有这个标记的 prompt
# （采集/分诊/probe/预测复盘）会安全降级成「整体走用户 prompt」，行为不变。
SPLIT_MARK = "以上是每篇都一样的规则（缓存前缀到此为止）。"

# 空目录：跳过项目 CLAUDE.md 与 skills 的自动发现。
# 放在 ~/.claude/ 下而不是 /tmp，是因为 /tmp 会被系统定期清理，
# 而这个目录必须一直存在 —— 不存在时 subprocess 直接抛 FileNotFoundError。
BARE_CWD = Path.home() / ".claude" / "headless-cwd"


def ensure_cwd() -> Path:
    """返回可用的空工作目录；建不出来就退回 home（宁可多花 token 也不能崩）。"""
    try:
        BARE_CWD.mkdir(parents=True, exist_ok=True)
        return BARE_CWD
    except OSError:
        return Path.home()


def split_prompt(prompt: str) -> tuple[str, str]:
    """在 SPLIT_MARK 处切成 (静态段, 动态段)。

    找不到标记 → 返回 ("", prompt)，整体当动态段走，等于不做拆分。
    这个降级是有意的：没接入拆分的链路不该因为这个函数而改变行为。
    """
    i = prompt.find(SPLIT_MARK)
    if i < 0:
        return "", prompt
    j = prompt.find("\n\n", i)          # 分隔块之后的第一个空行
    if j < 0:
        return "", prompt
    return prompt[:j], prompt[j + 2:]


def build_argv(claude_bin, prompt: str, extra=()) -> list:
    """拼出 subprocess 用的完整 argv，自动把静态前缀挪进 --append-system-prompt。

    六个调用点（写稿/审核/probe/采集/分诊/预测复盘）统一走这里，
    省得每处各拼一遍 —— 上一版就是因为拼在六个地方，加一个 flag 要改六处。
    """
    static, dynamic = split_prompt(prompt)
    argv = [str(claude_bin), *HEADLESS_FLAGS]
    if static:
        argv += ["--append-system-prompt", static]
    return argv + [*extra, "-p", dynamic]
