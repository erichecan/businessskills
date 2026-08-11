# Token 消耗优化 · 任务台账（2026-08-11 起）

依据：`docs/20260811-token消耗测算与优化.md`（实测 469 个会话 jsonl）。
基线：自动化日成本 $127，周额度 $1,350–1,500，每周瘫 1–2 天。
目标：自动化日成本 → $45 以内，不再出现整天停摆。

**台账是进度的唯一真相。每完成一条：验证 → 提交 → 回写 `[x] + commit hash`。**

---

## T1 撞 weekly limit 立刻停手，不进 5 小时重试循环
- [ ] 未开始
- **问题**：`refine_loop.py:468` 和 `independent_audit.py:87` 的 `LIMIT_RE` 不区分
  session limit（5h 窗口，该等）和 weekly limit（要等到周日，不该等）。
  实测 08-10 全天 112 次调用、0 token、0 产出，就是每次都熬满 5 小时。
- **验收**：
  1. 喂入含 `You've hit your weekly limit · resets 12am` 的假输出 → 函数立即返回，
     `time.sleep` 零调用
  2. 喂入含 `You've hit your session limit · resets 4am` 的假输出 → 仍走等待路径
  3. 两个脚本行为一致
- **产出**：`scripts/xhs-loop/refine_loop.py`、`scripts/xhs-health/independent_audit.py`、
  新增 `scripts/xhs-health/test_limit_handling.py`
- **依赖**：无

## T2 审核 prompt 按成稿反查筛料
- [ ] 未开始
- **问题**：审核 prompt 148k 字，其中三个整库占 89%（评论区原话 70.8k / 词库 33.9k /
  案例库 26.6k）。审核是**核对**不是**选材**——只需验证成稿里出现的原话在不在库里。
- **改法**：从成稿正文反查（最长公共子串 ≥6 字 = 照抄命中，4–5 字 = 疑似改写），
  强命中全留，弱命中补满预算，词库只给本篇关键词那一行。
- **验收**：
  1. 成稿里照抄的原话，**100% 出现在筛后料包里**（否则审核员会把"库里没有"
     误判成"编造原话"扣可信度分——这是最大风险）
  2. prompt 字数 148k → 40k 以下
  3. prompt 里如实声明这是筛过的子集及筛选规则（⛔ 不得谎报为整库）
  4. 同一篇稿筛前/筛后各审一次，总分差 ≤3 分
- **产出**：`scripts/xhs-health/independent_audit.py`、`scripts/xhs-health/test_audit_pack.py`
- **依赖**：无

## T3 probe / auto_analyze 的限额处理对齐
- [ ] 未开始
- **问题**：`auto_analyze.py` 也调 claude，需确认是否有同样的 weekly limit 空转问题。
- **验收**：三个调用点用同一套限额判定；weekly 立即停、session 才等
- **产出**：`scripts/xhs-probe/auto_analyze.py`
- **依赖**：T1（复用同一个判定函数）

## T4 审核 / probe 改走 Messages API 直调
- [ ] 未开始
- **问题**：审核 prompt 约 100k token，实测写缓存 311k token——多出来的是
  Claude Code CLI 注入的 system prompt、工具定义、skills 列表，对单轮纯文本任务无用。
  且 CLI 用 1h TTL 缓存（2× 价），而审核读/写缓存比只有 0.02，等于付 2 倍价钱写了没人读的缓存。
- **验收**：
  1. 同一 prompt 直调 vs CLI，输出格式一致、可解析
  2. 实测写缓存 token 下降 ≥50%
  3. 保留限额重试语义（429 / rate_limit_error）
- **产出**：`scripts/lib/claude_api.py`（共用）、`independent_audit.py`
- **备注**：需 `ANTHROPIC_API_KEY`；若只有订阅无 API key，此项作废并说明
- **依赖**：T1、T2

## T5 写稿把机械检查移出会话
- [ ] 未开始
- **问题**：写稿单次 24 次请求 / 14 次 Bash，prompt 砍 58% 成本只降 18%，
  钱在会话内工具往返累积的上下文。
- **验收**：请求数/次 24 → ≤8；成稿质量（独立审核分）不低于改前基线
- **产出**：`scripts/xhs-loop/refine_loop.py`
- **依赖**：T2（先确认审核链路稳定，才能用它衡量写稿质量没退化）

## T6 产能与发布配额对齐
- [ ] 未开始
- **问题**：`auto_publish.py:74` `DAILY_QUOTA=3`，写稿仍 2 次/天 ×4 篇 = 8 篇/天，过剩 2.7 倍。
- **验收**：日产 ≤4 篇；`com.eric.xhswrite.plist` 注释说明依据
- **产出**：`scripts/xhs-loop/com.eric.xhswrite.plist`
- **依赖**：T5（先降单次成本，再定产能）

---

## 硬停止条件（满足任一立即停下报告）
- 同一个问题连续 2 次没修好 → ⛔ 停，不试第 3 次
- 需要删除/覆盖非本次生成的重要文件
- 需要 API Key 等外部凭证而项目里没有
- 台账与实际代码状态对不上

## 遗留 / 未解决
- 交互式开发会话 8 天 $895（最大单项），与自动化共用同一周额度。属流程外，
  已在测算文档记录，本台账不处理。
