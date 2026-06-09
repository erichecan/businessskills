# businessskills (eric-skills) — Project Index

> Eric 的小红书内容创作工具箱。基于 dontbesilent2025 的商业方法论，结合 Claude Code Skills 实现诊断式内容创作辅助。Fork 自 dbskill，加入 Eric 原子库和代写模式。

## 技术栈

| 层 | 技术 |
|---|---|
| 核心 | Claude Code Skills（`.md` 指令文件） |
| Web App | Next.js App Router · TypeScript · Tailwind CSS · shadcn/ui |
| 数据库 | PostgreSQL（Neon）· Prisma ORM |
| 内容生产 | 小红书图文、XHS 封面 HTML 预览 |

## 项目结构

```
businessskills/
├── CLAUDE.md           # 文件组织规范（⛔ 根目录白名单、日期命名规则）
├── README.md           # 工具箱说明 + Skills 使用手册
├── VERSION             # 版本号
├── app/                # Next.js Web App（eric-skills 网页版）
│   ├── src/            # 页面和组件
│   └── prisma/         # DB schema
├── skills/             # Claude Code Skills 指令文件
├── workflow/           # 工作流文档
├── docs/               # 工作文档（YYYYMMDD- 命名）
├── preview/            # HTML 预览文件（YYYYMMDD- 命名）
├── xhs/                # 小红书图文内容（YYYYMMDD-topic/ 目录）
├── scripts/            # 一次性脚本（用完删除）
├── test_images/        # 测试产出
└── 知识库/             # 原始知识素材
```

## 可用 Skills

| Skill | 用途 |
|---|---|
| `/eric` | 主入口，自动路由 |
| `/eric-diagnosis` | 商业模式诊断 |
| `/eric-benchmark` | 对标分析（五重过滤） |
| `/eric-content` | 内容创作诊断 + 代写初稿 ⭐ |
| `/eric-hook` | 短视频开头优化 |
| `/eric-xhs-title` | 小红书标题（75 个爆款公式） |
| `/eric-action` | 执行力诊断（阿德勒框架） |
| `/eric-deconstruct` | 概念拆解（维特根斯坦式） |
| `/chatroom-austrian` | 奥派经济聊天室 |
| `/eric-upgrade` | 升级到最新版本 |

## 架构要点

- **文件组织严格规范**（见 CLAUDE.md）：非代码文件必须放子目录且以 `YYYYMMDD-` 开头，根目录只允许 5 个文件
- **双形态**：Skills（CLI 工具）+ Web App（浏览器交互），两者独立，均可使用
- **内容定位**：不卖解决方案，卖诊断；击中中年/蓝领经济焦虑群体
