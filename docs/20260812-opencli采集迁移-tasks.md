# 20260812 · opencli 采集迁移台账

## 背景（根因，不是现象）

今天三条链路报的是三种症状，实际是**两个根因**：

| 症状 | 报的错 | 真根因 |
|---|---|---|
| probe 18:30 | 「连续 2 个词返回空结果，判定被限流」 | 9333 profile **未登录** www 域，搜索页是登录墙 |
| probe 22:01 | 「触发安全验证，本轮立即终止」 | 同上，登录墙文案命中 `CAPTCHA_RE` 误报 |
| fetch_stats 21:15 | `HTTP Error 400` | 今天新写的 `JS_DETAIL_METRICS` 未经真机验证，与登录无关 |

实测证据（2026-08-12 22:0x）：

- 9333 专用 profile（`~/.xhs-chrome-profile`）的 `www.xiaohongshu.com` 只有游客 cookie
  （`a1/webId/gid/ets/…`，**无 `web_session`**）→ explore 能看 34 张卡片，搜索页直接登录墙
- 同 profile 的 `creator.xiaohongshu.com` **是登录的**（页面显示「Eric | 职场潜台词翻译官」）
  → 所以 22:00 的 auto_publish 能正常判断闸门，不受影响
- 日常 Chrome 的登录态完好，`opencli xiaohongshu whoami` → `logged_in: true`，粉丝 1775

结论：**cookie 不跨 profile 共享，且同一 profile 下两个域各自独立**。
CLAUDE.md 已新增「联网抓取优先走 opencli」规则，本次按该规则迁移。

## opencli 能力核对（已实测通过）

| probe 需要的字段 | opencli 命令 | 状态 |
|---|---|---|
| `top_notes`（rank/title/author/likes/url/published_at） | `xiaohongshu search <kw> -f json` | ✅ |
| `note_bodies`（正文） | `xiaohongshu note <signed_url> -f json` | ✅ |
| `engage_samples`（likes/collects/comments） | 同上，一次返回 | ✅ |
| `comments`（评论原话） | `xiaohongshu comments <signed_url> -f json` | ✅ |
| `density` | 本地 `judge_density()`，不联网 | ✅ 直接复用 |

⚠️ **`note` / `comments` 必须传带 `xsec_token` 的完整签名 URL**，裸 note-id 报
`ARGUMENT: now requires a full signed URL`。URL 从 `search` 结果原样透传，不要裁剪参数。

## 任务

- [x] T1 写 `scripts/xhs-probe/probe_opencli.py`，产出与 `probe.py` 同格式的 JSON
      验收：✅ 「HR问'你的缺点'该怎么回答」跑出 `full`，19 笔记 / 5 正文 / **49 评论**
            （CDP 版同类词只有 24 条评论）；顶层 key 与 `top_notes/note_bodies/comments/density`
            结构全部一致，仅 `engage_samples.dom_comments` 缺失，已补为「本次实际取到条数」
            `density.verdict=中` 由 `probe.judge_density()` 算出，未新写判据
      产出：scripts/xhs-probe/probe_opencli.py

- [ ] T2 补跑今天剩余 3 词
      词：如何快速提高表达能力？提升职场上限 / 如何通过表达建立主体性，像自己那样说？ /
          HR问'你的缺点'该怎么回答(一篇讲清楚)
      验收：3 份 `probe_20260812_*.json` 落盘且 `completeness=full`；
            `.probe_state.json` 清空
      依赖：T1

- [x] T3 修 `fetch_stats.py` 的 `JS_DETAIL_METRICS`，改走 `creator-note-detail`
      验收：✅ 直调 `fetch_note_detail(None, '6a6f6dd4…')` 返回
            `{'观看':'1177','点赞':'31','评论':'0','收藏':'41','分享':'4'}` +
            `{'ok':True,'ratio':'3.3%'}` —— 5 个指标齐全，且拿到了 health_check
            一直在报「发布满 7 天未回填」的搜索来源占比
      顺带：删掉已无引用的 `JS_DETAIL_METRICS` 常量，消除 SyntaxWarning
      ⚠️ 自己的笔记用**裸 note_id**，与 `note`/`comments` 要签名 URL 的规则相反

- [x] T4 重跑数据回流 `fetch_stats.py`
      验收：✅ 退出码 0，不再抛 HTTP 400。「已补抓满 7 天数据 3 条 → 发布数据.csv」：
            晋升答辩被评委问倒（10天）观看1177/赞31/收藏41/分享4 ·
            第三轮hr面试一般会问什么（10天）观看159/赞3/收藏1 ·
            汇报被领导打断怎么接（7天）观看61/赞5/收藏5
            —— 正是 health_check 一直在报「发布满 7 天未回填」的那 3 篇

- [x] T5 给 `auto_analyze.py` 接 Gemini 首轮 + Claude 回退（Eric 2026-08-12 选定）
      验收：✅ 单条实测「面试靠谱回答如果没被录用怎么办」→ 打印
            「由 Gemini 分析（未消耗 Claude 额度）」，disposition=做、密度=中，
            空缺具体且带证据，JSON 一次解析通过
      产出：scripts/gemini_cli.py（新）、scripts/xhs-probe/auto_analyze.py（改）
      设计：`run_model()` 先 Gemini 后 `run_claude()`；`XHS_ENGINE=claude` 可强制走旧路径。
            额度打满走 `QuotaExhausted` → **静默降级**继续把词分析完，不当失败丢任务
      key：`~/.gemini_api_key`（600，repo 之外，launchd 无 shell env 只能读文件）

- [x] T6 跑完剩余 6 个积压 probe 分析 → `backfill.py` 回填词库
      验收：✅ 6/6 全部由 Gemini 完成，`--list` 归零；回填 3 个日期，
            词库新增 9 个候选词 + 9 条评论原话
      值得记一笔：6 条里 4 条判「缓」，理由全部是原始数据 `no_results_or_blocked` ——
      那批 08-10 的 probe 本身就是登录墙时期采的空壳。Gemini 严格守住了
      DISPOSITION_RULE（「缓」只许用于数据缺失），没有含糊其辞地编空缺。
      ⚠️ 这 4 个词需要用 opencli 重采一次，现在的「候选」状态是数据坏了不是词不好。

- [x] T7 `daily_probe.sh` 第 38 行改调 `probe_opencli.py`
      为什么必须做：不改的话**明天 10:00 的定时任务会原样重演今天**——
      CDP 版永远连 9333 那个没登录的 profile。
      验收：✅ `bash -n` 通过；`/usr/bin/python3`（3.9，launchd 实际用的解释器）
            可编译并导入 `probe_opencli.py`
      注释里留了退回旧路径的条件，不是无脑替换

- [x] T8 补上「搜索来源占比」从未回写词库的链路断点
      发现过程：T4 明明补抓成功，health_check 却照旧报那 3 篇未回填 → 追下去发现
      `fetch_aged_stats` 只写 `发布数据.csv`，而 health_check 查的是
      **`词库.csv`** 的同名列（health_check.py:138）。**两张表从来没打通**，
      所以这条告警从存在起就不可能消除 —— 数据一直抓得到，只是没送到被检查的那张表。
      验收：✅ 新增 `backfill_ciku_ratio()` 并接进 main；历史数据一次性回填
            晋升答辩被评委问倒=3.3%、第三轮hr面试一般会问什么=1.3%；
            health_check 该条告警 **3 篇 → 1 篇**
      剩下那 1 篇「汇报被领导打断怎么接」是平台真没生成（观看仅 61，低于门槛），
      属事实不是缺陷，不要再去"修"它。

## 未决

- `probe.py` 的 `CAPTCHA_RE` 会把登录墙文案误判成安全验证，导致日志误导排查方向。
  修不修待定 —— 如果 probe 整体迁到 opencli，这条就自然消失。
