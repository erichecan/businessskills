#!/usr/bin/env python3
"""Gemini 免费层调用 —— 首轮跑这个，撞额度/出错再回退 claude -p。

## 为什么值得接

`claude -p` 每次调用有 **38,704 token 的固定开销**（CLAUDE.md + 全套工具定义 +
skills 列表 + system prompt，实测见 headless_cli.py 的注释）。走 Gemini HTTP API
没有这一层，同样一份 prompt：

    探词分析   claude -p: 38.7k 固定 + 17.0k 内容 ≈ 55.7k    Gemini: 17.0k（免费）
    写稿       claude -p: 38.7k 固定 + 33.8k 内容 ≈ 72.5k    Gemini: 33.8k（免费）

2026-08-12 用 auto_analyze 的原封 prompt 做过 A/B：同一条词
「向上汇报的关键，根本不在于你讲什么」，Claude 与 gemini-3.6-flash / 3.5-flash-lite
三方的 disposition（做）、density_echo（中）、evidence（strong）完全一致，
且都指向同一个空缺（体制内/助理岗、领导只认自己思路），Gemini 还引到了同一条评论原话。

## 免费层的硬边界（2026-08-12 实测）

- **Flash 全系可用**：3.6-flash / 3.5-flash / 3.5-flash-lite / 3-flash-preview / 3.1-flash-lite
- **Pro 全系额度为 0**：gemini-3.1-pro-preview、gemini-pro-latest 连一句 "hi" 都直接 429
  → 所以返工/评审这类要 Pro 级判断的活，仍然只能用 Claude。分工不是偏好，是硬约束。
- **gemini-2.5 系列对新 key 已下线**（404 "no longer available to new users"），别写旧型号名

## 判断权边界

这层只换「谁来生成」，不换「按什么判」。prompt 里写死的判据（disposition 规则、
density 以脚本 verdict 为准）原样透传，调用方的校验也一条不减 ——
换引擎不能顺手放宽判据，否则新旧两批结果不可比。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

KEY_FILE = Path.home() / ".gemini_api_key"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"

# 首选 3.6-flash（带 thinking，判断更稳）；它撞额度或抽风时退到 lite（快 5 倍，实测判断同样正确）。
MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
TIMEOUT = 300


class QuotaExhausted(RuntimeError):
    """免费层额度打满。调用方据此回退 claude -p，而不是当成普通失败重试。"""


def api_key() -> str:
    """env 优先，其次 ~/.gemini_api_key —— launchd 起的进程没有 shell env，只能读文件。"""
    k = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if k:
        return k
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


def available() -> bool:
    return bool(api_key())


def _call(model: str, prompt: str, key: str, temperature: float, max_tokens: int) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }).encode()
    req = urllib.request.Request(ENDPOINT.format(m=model, k=key), data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read())
    cands = d.get("candidates") or []
    if not cands:
        # 安全过滤或空回复。当普通失败处理，让调用方决定重试还是回退。
        raise RuntimeError(f"无 candidate：{json.dumps(d, ensure_ascii=False)[:200]}")
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if "text" in p).strip()


def run(prompt: str, temperature: float = 0.3, max_tokens: int = 16384) -> str:
    """跑一次。全部模型都撞额度 → QuotaExhausted；其它错误 → RuntimeError。"""
    key = api_key()
    if not key:
        raise RuntimeError(f"没有 API key（env GEMINI_API_KEY 或 {KEY_FILE}）")

    last = None
    quota_hit = 0
    for m in MODELS:
        try:
            return _call(m, prompt, key, temperature, max_tokens)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read() or "{}").get("error", {}).get("message", "")[:160]
            except Exception:
                pass
            if e.code == 429:
                quota_hit += 1
            last = RuntimeError(f"{m} HTTP {e.code}: {detail}")
        except Exception as e:                       # noqa: BLE001 超时/网络/无 candidate
            last = RuntimeError(f"{m}: {e}")

    if quota_hit == len(MODELS):
        raise QuotaExhausted(str(last))
    raise last or RuntimeError("Gemini 调用失败")
