#!/usr/bin/env python3
"""headless `claude -p` 的调用参数 —— 写稿/审核/probe 共用。

## 为什么要管这个：固定开销 46,091 token

2026-08-11 实测，一句 5 个字的 prompt（"只回复两个字：收到"）在项目目录下
用默认参数调用，输入侧就是 **46,091 token**。这不是我们的内容，是 CLI 每次
注入的：CLAUDE.md（全局 + 项目）、全套工具定义、skills 列表、system prompt。

三档实测：

    项目目录 + 全工具（原状）      46,091 token
    禁用工具                       41,849 token   −9%
    空目录 + 禁用工具              38,704 token   −16%

CLAUDE.md 和 skills 是**按工作目录发现**的，所以把 cwd 指到一个空目录就能
跳掉项目那份。全局 ~/.claude/CLAUDE.md 仍会加载 —— 要连它一起跳只有 `--bare`，
但 `--bare` 同时跳过 keychain 读取，直接 "Not logged in · Please run /login"，
订阅登录态拿不到，**不可用**。所以 38,704 是 CLI 路径下的地板价。

## 真正的收益不是这 7,387 token，是消掉多轮工具往返

写稿会话实测 24.4 次请求 / 14.1 次 Bash，而 999 次 Bash 命令里排前面的是：

    85×  cd .../scripts/xhs-health          31×  ls .../scripts/xhs-health
    25×  ls .../xhs/素材库/ | head            7×  head -60 draft_check.py
     6×  cat > /tmp/body.txt <<'TXT' …（自己数字数）

模型不是在跑机械检查（那早就由脚本在会话外跑，见 refine_loop 第 578 行），
而是在**找路**：找 draft_check.py 在哪、素材库里有什么、读检查脚本的源码。
诱因是 prompt 里那句「改完自己数一遍再输出」—— 它真的去找工具来数。

每多一轮工具往返，上下文就长一截、缓存就重写一次。写稿单次成本
$4.58 里，读缓存 $1.16 基本全是这么来的。

prompt 是自包含的（素材全喂进去了），成稿文件由脚本 save() 写，
模型根本不需要任何工具。禁掉即可。
"""
from pathlib import Path

# 工具名必须是 CLI 认得的，写错会直接报 "matches no known tool" 并拒跑。
# ⛔ 必须用逗号分隔成**一个**参数：--disallowedTools 是变长参数（<tools...>），
# 空格分隔会把后面的 prompt 也吞进去当工具名。
_DISALLOWED = ",".join([
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit",
])

HEADLESS_FLAGS = ["--disallowedTools", _DISALLOWED]

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
