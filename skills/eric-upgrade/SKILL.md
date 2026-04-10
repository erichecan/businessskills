---
name: eric-upgrade
description: 升级 eric-skills 到最新版本
trigger: /eric-upgrade、/升级、「升级工具箱」
---

# eric-upgrade

升级 eric-skills 到最新版本，显示更新内容。

## 使用场景

- 用户主动调用 `/eric-upgrade` 升级
- 显示版本变化和更新内容

## 升级流程

### Step 1: 检测安装位置

```bash
if [ -d "$HOME/.claude/skills/eric" ]; then
  INSTALL_DIR="$HOME/.claude/skills"
  echo "Install location: $INSTALL_DIR"
else
  echo "ERROR: eric-skills not found in ~/.claude/skills/"
  exit 1
fi
```

### Step 2: 获取当前版本

```bash
OLD_VERSION=$(cat "$HOME/.claude/skills/eric-upgrade/../../VERSION" 2>/dev/null || echo "unknown")
echo "Current version: $OLD_VERSION"
```

### Step 3: 获取远程版本

```bash
REMOTE_VERSION=$(curl -sL https://raw.githubusercontent.com/erichecan/businessskills/main/VERSION || echo "")
if [ -z "$REMOTE_VERSION" ]; then
  echo "ERROR: Cannot fetch remote version"
  exit 1
fi
echo "Remote version: $REMOTE_VERSION"
```

### Step 4: 比较版本

如果 `OLD_VERSION` 等于 `REMOTE_VERSION`，告诉用户已是最新版本，结束。

否则继续升级。

### Step 5: 备份当前版本

```bash
BACKUP_DIR="$HOME/.claude/skills/.eric-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r "$HOME/.claude/skills"/eric* "$BACKUP_DIR/" 2>/dev/null || true
echo "Backup created: $BACKUP_DIR"
```

### Step 6: 下载最新版本

```bash
TMP_DIR=$(mktemp -d)
git clone --depth 1 https://github.com/erichecan/businessskills.git "$TMP_DIR/eric-skills"
if [ $? -ne 0 ]; then
  echo "ERROR: Failed to clone repository"
  exit 1
fi
echo "Downloaded to: $TMP_DIR/eric-skills"
```

### Step 7: 替换旧版本

```bash
rm -rf "$HOME/.claude/skills"/eric*
cp -r "$TMP_DIR/eric-skills/skills"/eric* "$HOME/.claude/skills/"
rm -rf "$TMP_DIR"
echo "Upgrade completed"
```

如果复制失败，从备份恢复：

```bash
if [ $? -ne 0 ]; then
  echo "ERROR: Upgrade failed, restoring from backup..."
  rm -rf "$HOME/.claude/skills"/eric*
  cp -r "$BACKUP_DIR"/* "$HOME/.claude/skills/"
  echo "Restored from backup"
  exit 1
fi
```

### Step 8: 显示更新内容

读取 `$HOME/.claude/skills/eric/../../README.md`（如果存在），提取从 `OLD_VERSION` 到 `REMOTE_VERSION` 之间的更新内容。

格式：

```
eric-skills v{REMOTE_VERSION} — 从 v{OLD_VERSION} 升级成功！

更新内容：
- [从 README 提取的更新要点]

升级完成！
```

### Step 9: 清理备份

询问用户是否删除备份：

```bash
echo "Backup location: $BACKUP_DIR"
echo "Keep backup? (will be auto-deleted in 7 days if not used)"
```

不强制删除，让用户自己决定。

## 错误处理

- 网络失败：提示用户检查网络连接
- Git clone 失败：从备份恢复
- 文件复制失败：从备份恢复

## 注意事项

- 只支持通过 `~/.claude/skills/` 安装的版本
- 升级前自动备份，失败时自动恢复
- 不需要用户手动操作 git
