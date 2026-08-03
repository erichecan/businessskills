# 成稿 loop 与素材自动化 · 任务台账

> 起于 2026-08-02 会话。回答 Eric 三问：①采集自动化 ②选题成稿自动 loop ③采集喂素材库。
> **台账是进度的唯一真相，对话不是。** 每条做完回写 `- [x]` + commit hash。

## 背景判断（已确认的事实，不要重新推导）

- 采集**已经自动化**：claude.ai 本机 scheduled task「Xiaohongshu career interview digest」每 6 小时，
  就是 `运行日志.csv` 里 run1–run4 的来源。云端 routines 列表为空（`RemoteTrigger list` → `[]`），
  两个任务都是「run on your computer」模式，API 看不到也改不了 prompt。
- 采集**不能迁云端**：抓小红书需要本地登录态 Chrome + CDP 代理 :3456，云端沙箱没有。形态已经是对的。
- 成稿 loop **已经存在但是假闭环**：任务「Xiaohongshu content repurpose」每日 8:30 自称「打分循环至90分」，
  独立审核回来实际 72–86 分，11 篇只有 1 篇过闸门。病根是自评当裁判（D7 已定：自评无处置权）。
- **素材来源不设限**（2026-08-02 Eric 纠正）：素材是谁的不重要，能共鸣就行。
  真红线只有两条：**不能编造**、**不能把别人的经历说成"我的"**。
  → 案例库应开放采集来源，用「来源」列区分，成稿时按来源决定人称。

## 任务

- [x] T1 `draft_check.py` 加 `--file` 单篇模式
      验收：`python3 draft_check.py --file 成稿_x.md` 只检查该篇；退出码 0/1；
            `--days` 行为不变（health_check 现有调用不受影响）
      产出：scripts/xhs-health/draft_check.py
      依赖：无
      完成：检查逻辑抽成 check_one()，--file 走同一函数。实测：单篇通过退 0 / 文件不存在退 2 /
            --days 2 仍报 11 篇通过（行为不变）。跨篇查重仍用全量 all_drafts，未改语义。

- [x] T2 `refine_loop.py` 成稿质量闭环
      验收：跑一轮能完成 选题→成稿→机械检查→独立审核→(不过则返工)→落盘；
            返工必须换独立进程（不在同一会话自评自改）；
            轮次上限 3，仍不过写入 待激活素材库.csv；
            过线阈值 85（不是 90 —— 独立审核口径下 21 篇历史稿最高 86，定 90 会空转到上限）
      产出：scripts/xhs-loop/refine_loop.py
      依赖：T1
      完成：commit 2e4d180 —— 含 --dry-run/--topic/--rounds/--threshold；选题去重查词库+成稿+发布日志

- [x] T3 案例库开放采集来源
      验收：案例库.csv 有「来源」「来源链接」两列且旧 19 行标为 自有；
            harvest_cases.py 能从 评论区原话.csv 提出候选案例行（标 来源=采集 状态=待确认）；
            必须命中清单.md 写死人称规则：来源=自有→可第一人称；来源=采集→必须"有人说/评论区看到"
      产出：xhs/素材库/案例库.csv、scripts/case-entry/harvest_cases.py、必须命中清单.md
      依赖：无
      完成：commit 07f6d3e —— 采集 24 条候选（20 高分+4 中分）；清单加第 15 条人称红线

- [x] T4 `health_check.py` 补三条采集回执
      验收：①当天 run1–run4 缺轮次告警 ②每轮评论区原话 <2 条告警 ③候选词积压 >200 告警
      产出：scripts/xhs-health/health_check.py
      依赖：无
      完成：commit 1b9b0e7 —— 实测三条全部告警（缺 run1-4/原话欠收/积压 674）

- [ ] T5 接线：8:30 scheduled task 改为触发 refine_loop
      验收：任务 prompt 瘦身为触发本地脚本；跑一天后 审核记录.csv 出现 refine_loop 产的稿
      产出：（需 Eric 在 claude.ai 界面操作）
      依赖：T2 完成且实测跑通
      ⛔ 阻塞：需要 Eric 提供 8:30 任务当前的 prompt 原文

## 未解决问题

- 发布环节「定时开关点击未生效」（2026-08-02 日志），auto_publish 实际只到预填，
  定时+发布全人工。不在本轮范围，但会拖住 loop 的下游。
- `待激活素材库.csv` 目前只有 2 行，T2 会持续往里写，需要定期清理策略（暂不做）。
