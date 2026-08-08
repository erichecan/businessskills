---
name: eric-upgrade
description: |
  eric 工具箱更新器。两件事：① 同步上游 dontbesilent2025/eric 的最新方法论（跑 scripts/sync-upstream.sh，生成待审查分支）；② 把本仓库 skills/ 的最新版本刷新到本机 Claude Code。用户说「更新 eric」「升级 eric」「同步上游」「检查 eric 更新」或输入 /eric-upgrade 时使用。
  Update Eric's toolkit: sync upstream eric methodology via sync script, and refresh local skill links.
---

# eric-upgrade：更新 eric 工具箱

本工具箱是 [dontbesilent2025/eric](https://github.com/dontbesilent2025/eric) 的 fork（仓库：erichecan/businessskills），含大量 Eric 自有改造。更新分两层，不要混用。

## 层 1：同步上游方法论（eric → 本仓库）

**唯一入口是仓库自带的同步脚本**，不要直接 git merge 上游（两边历史无关，且本地有自有改动）：

```bash
cd <本仓库根目录>
bash scripts/sync-upstream.sh
```

脚本行为：

- 拉取 `dontbesilent2025/eric` 最新 main
- 新增 skill：自动复制并做 eric→eric 改名
- 已有 skill 有变化：生成 `upstream-diffs/*.diff` 供人工审查，**不自动覆盖**（本地可能有 Eric 自有改动）
- 产出一个 `upstream-sync-YYYYMMDD` 分支，审查后自行合并到 main

**注意**：脚本只复制 `SKILL.md`。如果上游新 skill 带 `scripts/`、`tools/`、`templates/` 子目录，需要手动补齐完整目录，并检查 `/eric` 命令引用是否已改成 `/eric`（macOS 的 sed 不支持 `\b`，脚本的改名可能漏掉这类引用）。

## 层 2：刷新本机安装

本机通过软链使用（安装方式见 README）。仓库更新后，软链自动生效，无需重复安装。若有新增 skill 目录，补一次软链：

```bash
ln -s "<本仓库根目录>/skills"/* ~/.claude/skills/ 2>/dev/null
```

## 边界

- 用户只问「有没有更新」→ 只 `git fetch upstream main` 后对比 VERSION，报告差异，不执行同步。
- 不使用 `npx skills add dontbesilent2025/eric`（那会绕过 fork，覆盖 Eric 自有改造）。
- 同步分支合并前，`upstream-diffs/` 里的 diff 必须人工过目——上游可能删掉 Eric 依赖的段落（如知识包引用、web-access 调研步骤）。

## 语言

- 用户用中文就用中文回复，用英文就用英文回复
