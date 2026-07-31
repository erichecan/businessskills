# Eric 内容生产流水线（Web App）

小红书内容生产的本地工作台，4 步工作流：**收集热点 → 确定选题 → 创作内容 → 准备发布**。

所有 AI 步骤采用「人工中转 Claude」模式：API 的 `GET` 返回拼好的提示词（system prompt 来自仓库根目录 `skills/*/SKILL.md`），复制到 Claude 对话，再把回复粘贴回来由 `POST` 存库。

## 启动

```bash
npm install
npx prisma generate
npm run dev        # http://localhost:3000
```

## 技术栈

Next.js 16 (App Router) · React 19 · TypeScript · Tailwind CSS 4 · shadcn/ui · Prisma 7 + SQLite（libsql adapter）

**改代码前必读 `AGENTS.md`**：这个 Next.js 版本有 breaking changes，先查 `node_modules/next/dist/docs/`。

## 数据模型

`HotTopic`（热点）→ `Topic`（选题）→ `Content`（稿件）。三张表，见 `prisma/schema.prisma`。

- Prisma Client 生成到 `src/generated/prisma/`，导入路径 `@/generated/prisma/client`
- SQLite 路径配置在 `prisma.config.ts`（非 schema datasource url）；`prisma/dev.db` 不入 git

## 页面

| 路由 | 用途 |
|------|------|
| `/` | 仪表盘（4 步导航 + 内联面板） |
| `/trends` | 热点：手动录入 / 从素材库同步 / 相关性标记 |
| `/topics` | 选题：立项、批准、生成选题提示词（匹配 Eric 原子库） |
| `/content/[id]` | 内容工坊：初稿 → 定稿 → 内容诊断 → AI 检测 → 标题 |
| `/publish` | 发布：列出就绪内容，复制正文/标题 |
| `/video` | 视频流水线（调用 `../scripts/*.py`，与主流程数据独立） |

## 素材库接入

「从素材库同步」按钮把定时采集任务的产物导入数据库：

- `POST /api/scrape` `{platform:"xiaohongshu"}` — 读 `../xhs/素材库/职场面试_记忆库.csv` → `HotTopic`（url 优先 + 归一化标题兜底去重）
- `POST /api/import/topics` — 读 `选题库.csv` → `Topic`（已勾选→approved）

素材库目录可用环境变量 `SUCAI_DIR` 覆盖，默认 `../xhs/素材库`。

## 环境变量（`.env.local`）

| 变量 | 用途 |
|------|------|
| `ANTHROPIC_API_KEY` | 预留（当前流程为人工中转，未直接调用） |
| `RSSHUB_BASE_URL` | 微信公众号 RSSHub 抓取 |
| `OPENAI_API_KEY` | 视频流水线配音 |
| `SUCAI_DIR` | 素材库目录覆盖 |

设计文档见 `../xhs/职场表达力/工作SOP/20260625-小红书并入内容工坊-重构设计.md`。
