# 自动写稿环节接入 coverbig 封面 + 去 AI 味方法论

2026-09-10。改的是真正在跑的自动化生产流水线（`launchd` → `com.eric.xhswrite` →
`scripts/xhs-loop/refine_loop.py`），不是 Next.js 那个基本没人用的「中转 Claude」网页工坊。

## 生产流水线现状（背景）

```
10:00/14:30 probe 备料 → 11:00/15:30 write 写稿 → 13:00/17:30 audit 审核 → 22:00 发布
```

`write` 这一步跑的是 `refine_loop.py`：`build_prompt()` 拼出一份写稿 prompt 交给
`claude -p`，产出成稿 md + `cards.json`；红线/分数不过线时**复用同一个 `build_prompt()`**
带着审核反馈再跑一轮返工。也就是说，改 `build_prompt()` 一处，首稿和自动改写两个环节
都会生效。

## 改了什么

### 1. 封面卡型换成 coverbig（[[20260909-coverbig封面规范]] 的落地）

`build_prompt()` 原来教模型：

- 输出的 7 张卡 `type` 依次是 `cover,scene,contrast,quote,why,formula,boundary`
- 第 1 张 `cover` 卡**不要** pose（旧封面是纯文字+右下角小头像，放大姿势图等于一张卡两个人）

现在改成：

- `type` 依次是 `coverbig,scene,contrast,quote,why,formula,boundary`
- 第 1 张也必须选 pose，规则和其余 6 张一样——按内容/情绪选，不能因为「是封面」就
  兜底选 `pose1_站立`
- 新增一条：封面 `title` 里挑 3-5 字的判断/结论词，用 `<span class="hl">...</span>`
  包住做荧光笔高亮（对应 `card.html` 里 `.coverbig h1 .hl` 的 `white-space:nowrap`
  实现，高亮词必须短，写长了会溢出画布）

同步改了标题定向返工那条独立路径（`TITLE_FIX_PROMPT` / `title_fix_one`）：它单独改
`cards.json` 第 1 张的 title/body，不走 `build_prompt()`，所以要单独加一句「挑 3-5 字
包 `<span class="hl">`」，否则标题档返工出来的封面会退回没有高亮的旧样子。

`render_cards()`、`save()`、`draft_check.py`、`independent_audit.py` 都不依赖
`type` 字段的具体取值（抽查过 `cards[0]` 之类的引用），改字符串没有连带风险。

### 2. 去 AI 味方法论接进写稿静态规则区

装的 [tramstop-skill](https://github.com/alchaincyf/tramstop-skill)（电车站.skill）
核心判断：AI 味分四层（词汇<句式<结构<经验），病灶在结构层和经验层，光删词没用。

`build_prompt()` 里原来已经有真实素材注入的骨架（评论区原话.csv + 案例库.csv 按选题
筛选喂给模型），这部分其实就是 tramstop-skill「挖真实素材」那一步在这条流水线上的
对应实现。这次补的是它**没有**明确给到模型的部分：

- 结构层的具体病灶清单（金句收尾/导游式路标/完美闭环/否定排比/匀速句长）——
  之前完全没提过，唯一的机械检测 `draft_check.py` 只数「不是X是Y」句式出现次数，
  卡的是词汇/句式层，够不到 tramstop-skill 说的真正病灶
- 「换成另一条素材还成立吗」这个自检问句，引导模型在案例库/评论区原话里优先选
  不可替换的枝蔓细节，而不是查资料式的泛用例子
- 判断要押注、不确定要长在句子里、情绪不写标注词——这几条是 tramstop-skill
  「你相信什么」一节的原文原则

⛔ 没搬的东西：`references/checklist.md`（坏例子清单）。tramstop-skill 自己的「白熊
隔离」原则说这份清单是诊断用的，写作时脑子里装着坏例子清单反而容易写出清单里的
毛病，所以只把生成侧原则搬进 `build_prompt()`，清单留给以后如果要做独立诊断/审核
维度再用。

`REWORK_FIX_PROMPT`（定向正文返工）和 `MECH_FIX_PROMPT`（机械项修复）这两条更窄的
辅助路径没有改——它们只改「## 正文」节里的一两句话，不是完整写稿，加一整段方法论
成本不划算，而且这两条本来就会复用已经写好的正文，AI 味风险比全量写稿低。

## 验证

`python3 refine_loop.py --dry-run` 跑通，选中真实选题、拼出真实 55k 字 prompt 未报错；
另外直接调用 `build_prompt()` 抽查了新增的四处文本（去 AI 味红线段落、`coverbig` 类型
声明、封面必须选 pose、`class="hl"`）确认都在最终 prompt 里；`TITLE_FIX_PROMPT.format()`
补全参数试跑无异常。**没有跑一次真实的 `claude -p` 调用**——那要花真实 API 额度，
下一次 11:00/15:30 定时任务自然会用上这版新 prompt，出来的第一篇稿建议人工看一眼
封面高亮和 pose 选得对不对。

## 涉及文件

- `scripts/xhs-loop/refine_loop.py`：`build_prompt()`、`TITLE_FIX_PROMPT`
- 全局 skill：`~/.claude/skills/tramstop-skill/`（本次新装，不在本仓库内）
