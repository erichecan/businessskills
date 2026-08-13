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

## 第二轮（Eric 2026-08-12 22:4x 批准三件）

- [ ] T9 用 opencli 重采 08-10 那 4 个空壳词，重新分析回填
      词：职场万用术：你学会了吗？ / 面试不能透露个人信息包括哪些 /
          面试官是怎么看出来一个人能力差的 / 面试靠谱回答如果没被录用怎么办?
      ✏️ 更正：不需要删旧 `.result.json`。重采产出 `probe_20260812_*` 前缀，与 08-10
         的旧文件不同名，`pending()` 会自动把新文件捡进队列；旧的空壳记录留着当历史证据。
      验收：✅ 4/4 全部 full，评论 60/51/60/60 条。密度从清一色「待探测」变成真判断：
            职场万用术=高（中位 4897.5，低赞 0%）· 面试不能透露个人信息=**低**（92.5，50%）·
            面试官是怎么看出来能力差=高（3678）· 面试靠谱回答如果没被录用=**低**（115，47%）
            后两个判「低」= 搜索位有空缺，正是原本会被空壳数据埋掉的机会

- [x] T10 修 `probe.py` 的 `CAPTCHA_RE` 误报
      问题：登录墙里手机号登录框带「验证码」三个字，命中 `安全验证|验证码|滑动验证`，
            于是「没登录」被报成「触发安全验证」，把排查方向带偏一整天
      改法：`JS_PAGE_STATE` 加 `login` 判定并**前置**，captcha 显式排除登录场景、
            且不再拿「验证码」当风控特征；新增 `login_required` 分支，
            日志直接给出「creator 域和 www 域登录态独立」「改跑 opencli 版」的处置
      顺带：删掉 `CAPTCHA_RE`/`EMPTY_RE` 两个**从未被使用**的死常量 ——
            两处各写一份正则、只有 JS 那份生效，正是这个误判能活这么久的原因之一
      验收：✅ 用今天实测的三段真实文案跑判定：
            登录墙原文 → login_required · 真风控页 → captcha_triggered ·
            「没找到相关内容」→ empty，三种各归各位

- [x] T11 `refine_loop.py` 写稿接 Gemini（usage 大头，约 72.5k/次）
      分工判据用 **feedback 是否为空**，对应 Eric 原话「初步用 Gemini，返工用 Claude」：
        · feedback 空   = 从零写第一稿 → Gemini
        · feedback 非空 = 带审核报告定向返工 → Claude（这一环最吃理解力，改错地方比不改更糟；
          且 Gemini 免费层只有 Flash，Pro 全系额度为 0）
      验收：✅ 实跑「hr面试薪酬谈判有哪些技巧」，日志打印
            「write_r1 由 Gemini 出稿（未消耗 Claude 额度）」，parse_output 正常、成稿落盘，
            修掉 T12 那个检查器 bug 后**机械检查全项通过**

- [x] T12 修 `draft_check.py` 的 CTA 窗口取错位置（T11 验收时发现）
      根因：`body = re.sub(r"```.*?```","",text)` 是**去掉代码块的全文**，正文节是 `body_sec`。
      CTA 判定却用 `body[-260:]`，而成稿正文后固定跟着「图文卡片 / 话题标签 / 处置」
      三节元信息（稳定占 200+ 字），把真正的 CTA 每次都挤出窗口。
      报错文案一直写着「正文末 260 字」，量的却是文件末 260 字 —— 变量名骗了人。
      实测这篇：正文节内有 10 个 A/B/C 标记、正文节尾窗口命中 8 个，按全文尾窗口只命中 1 个。
      验收：✅ 改用 `body_sec or body` 后，今天三篇稿：
            综合评估没消息（Claude）通过 · HR面试薪酬谈判（Gemini）通过 ·
            向上汇报先分人再开口（Claude）**仍报正文 628 字超规格**
            —— 第三篇证明判据没被放宽，真违规照样抓得出来

## ⚠️ 重采时必踩的坑（T9 实操中撞到，记下来）

`write_result()` 只保护 **.json**（不许用更差的完整度覆盖更好的），
但 **.result.json 完全不受管**。于是重采同一个词时会出现：

    probe_xxx.json         22:45  ← 新数据（opencli，评论 60 条）
    probe_xxx.result.json  10:37  ← 旧分析（基于上午那份已被覆盖的数据）

而 `auto_analyze.pending()` 见到有 .result.json 就跳过 —— **新数据永远不会被重新分析**，
词库里留着的是对一份已经不存在的数据的判断。

这次的实际后果：「面试靠谱回答如果没被录用怎么办」旧分析 density_echo=**高**，
而新数据 judge_density 算出来是**低**（中位 115、低赞 47% = 搜索位有空缺）。
不重跑的话，一个有机会的词会被按「高密度」压掉。

处置：重采后把对应 `.result.json` 改名为 `.result.json.bak-<时刻>`
（`.bak` 后缀不被 `probe_*.json` 的 glob 捡到），再跑 auto_analyze。
不要直接删 —— 旧分析本身没错，只是对不上新数据了。

→ 值得考虑的根治：让 `write_result()` 覆盖 .json 时，一并把同名 .result.json 挪走。
   本轮没做，因为那会改动 probe.py 的既有行为，留给下一轮拍板。

## 未决

- `probe.py` 的 `CAPTCHA_RE` 会把登录墙文案误判成安全验证，导致日志误导排查方向。
  修不修待定 —— 如果 probe 整体迁到 opencli，这条就自然消失。
