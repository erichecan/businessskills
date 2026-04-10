# eric-skills

Eric 的小红书内容创作工具箱。诊断式内容 × AI 辅助 × 中年人经济焦虑的出路。

**定位：** 不卖解决方案，卖诊断。用内容击中一群人，把问题剖析清楚，消解问题本身。

**基础：** 这个仓库 fork 自 [dontbesilent2025/dbskill](https://github.com/dontbesilent2025/dbskill)，感谢 dontbesilent 把 12,307 条推文提炼成开放的商业方法论。在此基础上，我做了以下改造：
- 命名空间从 `dbs` 改成 `eric`
- 新增 Eric 原子库，积累自己的素材
- eric-content 新增代写模式
- 后续会持续根据自己的实践演化

---

## 工具箱

### eric 诊断工具

| Skill | 做什么 |
|---|---|
| `/eric` | 主入口，自动路由到对的工具 |
| `/eric-diagnosis` | 商业模式诊断。消解问题，不回答问题 |
| `/eric-benchmark` | 对标分析。五重过滤，排除噪音 |
| `/eric-content` | 内容创作诊断 + 代写初稿 ⭐ |
| `/eric-hook` | 短视频开头优化。诊断 + 生成方案 |
| `/eric-xhs-title` | 小红书标题公式。75 个爆款公式匹配 |
| `/eric-action` | 执行力诊断。阿德勒框架 |
| `/eric-deconstruct` | 概念拆解。维特根斯坦式审查 |

### chatroom 系列

| Skill | 做什么 |
|---|---|
| `/chatroom-austrian` 或 `/奥派` | 奥派经济聊天室。哈耶克 × 米塞斯 × Claude 三人对话 |

### 工具

| Skill | 做什么 |
|---|---|
| `/eric-upgrade` | 升级 eric-skills 到最新版本 |

### 工作流

```
diagnosis（商业模式对不对）
    ↓
benchmark（找谁模仿）
    ↓
content（内容怎么做 / 帮我写初稿）
    ↓ 发现开头问题        ↓ 需要标题
hook（开头怎么优化）    xhs-title（小红书标题公式）
    ↓
action（做不动怎么办）

deconstruct（随时拆概念）
```

Skill 之间会自动推荐下一步：
- diagnosis 发现心理问题 → 推荐 action
- content 发现开头问题 → 推荐 hook
- content 需要起标题 → 推荐 xhs-title
- xhs-title 标题选好 → 推荐 hook 优化开头
- benchmark 发现逃避执行 → 推荐 action

---

## 和原版 dbskill 的不同

### 1. eric-content 新增代写模式

原版 dbs-content 只做诊断，明确说「你不帮人写内容」。但对我来说，Claude 代写初稿 + 我出素材和修改 = 效率最高的组合。

所以 eric-content 现在有两种模式：

- **模式 A 诊断**（原有）：你发初稿，Claude 做五维检测，指出问题
- **模式 B 代写**（新增）：你给素材（经历 / 观点 / 案例 / 数据），Claude 组织语言写初稿，写完自动过一遍诊断

代写模式的硬约束：
- 素材不合格直接退回，不硬写
- 禁用 AI 特征词（「值得注意的是」「让我们」「请记住」等）
- 禁空洞排比和夸张情绪词
- 必须标注哪几处需要你本人补充真实细节

### 2. 新增 Eric 原子库

路径：`知识库/Eric原子库/`

原版的原子库是 dontbesilent 12,307 条推文的提炼，那是他的思想库。我新建了一个空的 Eric 原子库，用来积累我自己的观察、经历、客户案例、小红书内容。

格式基本照抄 dontbesilent 的，多了一个 `source` 字段（小红书/微信/对话/思考/客户案例），因为我的素材来源更多元。

怎么往里加：直接告诉 Claude「今天发生了 X 事，帮我提炼成原子」，它会按格式追加到 jsonl 里。

### 3. 后续迭代方向

- 往 Eric 原子库里持续填素材，形成自己的内容资产
- 根据实际使用调整 skill 的说话风格（默认还是 dontbesilent 的犀利风格，可能会演化成更贴近 Eric 自己的）
- 可能新增：小红书发布日历、数据复盘、选题筛选脚本

---

## 安装

**本地软链（推荐）：**

```bash
# clone 到本地
git clone https://github.com/erichecan/businessskills.git
cd businessskills

# 软链到 Claude Code 的 skills 目录
ln -s "$(pwd)/skills"/* ~/.claude/skills/
```

安装后在 Claude Code 中输入 `/eric` 即可。

---

## 知识库

知识库是完全开放的。不安装 Skill 也能用 —— 可以只拿走你需要的部分。

### 目录结构

```
知识库/
├── 原子库/                     # dontbesilent 原始原子库
│   ├── atoms.jsonl             # 4,176 个知识原子（全量）
│   ├── atoms_2024Q4.jsonl      # 按季度拆分
│   └── ...
│
├── Eric原子库/                  # Eric 自己的原子库（逐步积累）
│   ├── atoms.jsonl
│   ├── atoms_2026Q2.jsonl
│   └── README.md
│
├── Skill知识包/                 # 提炼后的方法论文档
│   ├── diagnosis_公理与诊断框架.md
│   ├── benchmark_对标方法论.md
│   ├── content_内容创作方法论.md
│   └── ...
│
└── 高频概念词典.md
```

### 原子库是什么

每个知识原子是一条结构化的知识点：

```json
{
  "id": "2024Q4_042",
  "knowledge": "判断一个生意能不能做，必要条件之一是你能不能说出这个产品的颜色",
  "original": "判断一个生意能不能做，必要条件之一是你能不能说出这个产品的颜色...",
  "url": "https://x.com/dontbesilent/status/...",
  "date": "2024-10-01",
  "topics": ["商业模式与定价", "语言与思维"],
  "skills": ["eric-diagnosis", "eric-deconstruct"],
  "type": "anti-pattern",
  "confidence": "high"
}
```

### 使用场景

- **给 AI 加商业诊断能力** —— 把 `知识库/Skill知识包/diagnosis_公理与诊断框架.md` 粘贴到 system prompt
- **做 RAG 知识库** —— 把 `atoms.jsonl` 导入向量数据库
- **只要案例** —— 只看 `type: "case"` 或 `type: "anti-pattern"` 的原子
- **做 chatbot** —— 用方法论做 system prompt，用原子库做 RAG 增强
- **学习研究** —— 按 `topics` 过滤感兴趣的领域

---

## 致谢

感谢 [@dontbesilent](https://x.com/dontbesilent) 开放了整套方法论和原子库。原版请查看 [dontbesilent2025/dbskill](https://github.com/dontbesilent2025/dbskill)。

---

## 许可证

继承原版的 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 许可证。

- 个人使用、学习、研究、非商业项目：不需要署名，不需要申请
- 公开发布衍生作品：请注明来源
- 商业用途：需要单独授权
