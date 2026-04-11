#!/bin/bash
set -e

# sync-upstream.sh
# 同步上游 dontbesilent2025/dbskill 的更新到新分支，不影响 main
# 策略：文件级对比 + 按需复制（不做 git merge，避免不相关历史冲突）
# 用法：bash scripts/sync-upstream.sh

UPSTREAM="https://github.com/dontbesilent2025/dbskill.git"
UPSTREAM_BRANCH="main"
BRANCH="upstream-sync-$(date +%Y%m%d)"
TMPDIR_UPSTREAM="/tmp/dbskill-upstream-$$"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
step()  { echo -e "${BLUE}▶  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; exit 1; }

cleanup() {
  rm -rf "$TMPDIR_UPSTREAM"
}
trap cleanup EXIT

echo "🔄 开始同步上游更新..."
echo ""

# ── 1. 确保在项目根目录 ──────────────────────────────────────────────────────
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

# ── 2. 检查是否已有同名分支（今天已运行过） ─────────────────────────────────
if git branch | grep -q "$BRANCH"; then
  warn "分支 $BRANCH 已存在，本次脚本已在今天运行过"
  warn "如需重新同步，删除分支后再运行："
  echo "  git branch -D $BRANCH"
  echo "  git push origin --delete $BRANCH"
  exit 0
fi

# ── 3. 注册/确认 upstream remote ─────────────────────────────────────────────
if git remote | grep -q upstream; then
  CURRENT=$(git remote get-url upstream)
  if [ "$CURRENT" != "$UPSTREAM" ]; then
    warn "upstream 地址不一致，更新为 $UPSTREAM"
    git remote set-url upstream "$UPSTREAM"
  fi
else
  step "添加上游 remote..."
  git remote add upstream "$UPSTREAM"
fi

# ── 4. 拉取上游 ──────────────────────────────────────────────────────────────
step "拉取上游内容..."
git fetch upstream "$UPSTREAM_BRANCH" --quiet

# ── 5. 把上游文件 checkout 到临时目录 ────────────────────────────────────────
step "检出上游文件到临时目录..."
mkdir -p "$TMPDIR_UPSTREAM"
git archive upstream/"$UPSTREAM_BRANCH" | tar -x -C "$TMPDIR_UPSTREAM"

# ── 6. 分析差异 ──────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "📊 差异分析"
echo "=========================================="

# 6a. 上游新增的 skill（dbs-xxx → eric-xxx 映射）
NEW_SKILLS=()
UPDATED_SKILLS=()

for upstream_skill_dir in "$TMPDIR_UPSTREAM/skills"/*/; do
  upstream_skill=$(basename "$upstream_skill_dir")
  # 计算对应的 eric 命名
  if [ "$upstream_skill" = "dbs" ]; then
    eric_skill="eric"
  elif [ "$upstream_skill" = "dbskill-upgrade" ]; then
    eric_skill="eric-upgrade"
  else
    eric_skill="${upstream_skill/dbs-/eric-}"
  fi

  if [ ! -d "$PROJECT_ROOT/skills/$eric_skill" ]; then
    NEW_SKILLS+=("$upstream_skill → $eric_skill")
  else
    # 比较内容是否有变化（用 dbs→eric 替换后对比）
    upstream_content=$(sed 's/dbs-/eric-/g; s/\/dbs\b/\/eric/g; s/dbskill/eric/g' "$upstream_skill_dir/SKILL.md" 2>/dev/null || echo "")
    eric_content=$(cat "$PROJECT_ROOT/skills/$eric_skill/SKILL.md" 2>/dev/null || echo "")
    if [ "$upstream_content" != "$eric_content" ]; then
      UPDATED_SKILLS+=("$upstream_skill → $eric_skill")
    fi
  fi
done

# 6b. 新的原子库季度文件
NEW_ATOMS=()
for atom_file in "$TMPDIR_UPSTREAM/知识库/原子库/"atoms_*.jsonl; do
  filename=$(basename "$atom_file")
  if [ ! -f "$PROJECT_ROOT/知识库/原子库/$filename" ]; then
    NEW_ATOMS+=("$filename")
  fi
done

# 输出分析结果
if [ ${#NEW_SKILLS[@]} -eq 0 ] && [ ${#UPDATED_SKILLS[@]} -eq 0 ] && [ ${#NEW_ATOMS[@]} -eq 0 ]; then
  info "上游没有新增内容，已是最新"
  exit 0
fi

if [ ${#NEW_SKILLS[@]} -gt 0 ]; then
  echo ""
  echo "🆕 新增 Skill（将自动复制并重命名）："
  for s in "${NEW_SKILLS[@]}"; do
    echo "   $s"
  done
fi

if [ ${#UPDATED_SKILLS[@]} -gt 0 ]; then
  echo ""
  echo "📝 已有 Skill 内容有变化（将生成 diff 文件供你审查）："
  for s in "${UPDATED_SKILLS[@]}"; do
    echo "   $s"
  done
fi

if [ ${#NEW_ATOMS[@]} -gt 0 ]; then
  echo ""
  echo "📚 新的原子库季度文件（将自动复制）："
  for f in "${NEW_ATOMS[@]}"; do
    echo "   $f"
  done
fi

echo ""
echo "=========================================="

# ── 7. 处理未提交的改动 ──────────────────────────────────────────────────────
STASHED=false
if ! git diff --quiet || ! git diff --cached --quiet; then
  step "临时 stash 未提交的改动..."
  git stash push -m "sync-upstream: auto stash $(date +%Y%m%d_%H%M%S)"
  STASHED=true
fi

# ── 8. 创建新分支 ────────────────────────────────────────────────────────────
step "从 main 创建新分支 $BRANCH..."
git checkout main --quiet
git checkout -b "$BRANCH" --quiet

# ── 9. 恢复 stash ────────────────────────────────────────────────────────────
if [ "$STASHED" = true ]; then
  git stash pop --quiet
  info "已在新分支上恢复之前的改动"
fi

# ── 10. 应用变更 ─────────────────────────────────────────────────────────────
DIFF_DIR="$PROJECT_ROOT/upstream-diffs"
mkdir -p "$DIFF_DIR"

# 10a. 复制新增 Skill
for entry in "${NEW_SKILLS[@]}"; do
  upstream_skill="${entry% → *}"
  eric_skill="${entry#* → }"
  step "复制新 Skill: $upstream_skill → skills/$eric_skill/"
  mkdir -p "$PROJECT_ROOT/skills/$eric_skill"
  # 复制并把 dbs 命名替换为 eric
  sed 's/dbs-/eric-/g; s/\/dbs\b/\/eric/g; s/dbskill/eric/g' \
    "$TMPDIR_UPSTREAM/skills/$upstream_skill/SKILL.md" \
    > "$PROJECT_ROOT/skills/$eric_skill/SKILL.md"
done

# 10b. 生成已更新 Skill 的 diff 文件
for entry in "${UPDATED_SKILLS[@]}"; do
  upstream_skill="${entry% → *}"
  eric_skill="${entry#* → }"
  diff_file="$DIFF_DIR/${eric_skill}.diff"
  step "生成 diff: $eric_skill → upstream-diffs/${eric_skill}.diff"
  # 先把上游内容做 dbs→eric 替换，再 diff
  upstream_converted=$(mktemp)
  sed 's/dbs-/eric-/g; s/\/dbs\b/\/eric/g; s/dbskill/eric/g' \
    "$TMPDIR_UPSTREAM/skills/$upstream_skill/SKILL.md" > "$upstream_converted"
  diff -u "$PROJECT_ROOT/skills/$eric_skill/SKILL.md" "$upstream_converted" \
    > "$diff_file" || true  # diff 有差异时返回 1，用 || true 忽略
  rm "$upstream_converted"
done

# 10c. 复制新的原子库季度文件
for filename in "${NEW_ATOMS[@]}"; do
  step "复制原子库: $filename"
  cp "$TMPDIR_UPSTREAM/知识库/原子库/$filename" "$PROJECT_ROOT/知识库/原子库/$filename"
done

# ── 11. git add & commit ─────────────────────────────────────────────────────
git add .
COMMIT_MSG="sync: upstream updates $(date +%Y-%m-%d)"
if [ ${#NEW_SKILLS[@]} -gt 0 ]; then
  COMMIT_MSG="$COMMIT_MSG | new skills: $(IFS=,; echo "${NEW_SKILLS[*]}" | sed 's/ → [^,]*//')"
fi
if [ ${#NEW_ATOMS[@]} -gt 0 ]; then
  COMMIT_MSG="$COMMIT_MSG | new atoms: ${#NEW_ATOMS[@]} files"
fi

if git diff --cached --quiet; then
  warn "没有新文件需要提交（可能只有 diff 文件）"
else
  git commit -m "$COMMIT_MSG"
  info "已提交变更"
fi

# ── 12. 推送到 origin ────────────────────────────────────────────────────────
step "推送分支到 origin..."
if git push origin "$BRANCH" 2>&1; then
  info "推送成功"
else
  warn "推送失败，分支已在本地创建，稍后可手动 push"
fi

# ── 13. 完成报告 ─────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
info "同步完成！"
echo ""
echo "📌 新分支：$BRANCH"
echo ""

if [ ${#NEW_SKILLS[@]} -gt 0 ]; then
  echo "🆕 已自动添加的新 Skill（dbs→eric 命名已转换）："
  for s in "${NEW_SKILLS[@]}"; do eric_skill="${s#* → }"; echo "   skills/$eric_skill/SKILL.md"; done
  echo ""
fi

if [ ${#UPDATED_SKILLS[@]} -gt 0 ]; then
  echo "📝 需要你审查的 Skill 变更（diff 文件在 upstream-diffs/）："
  for s in "${UPDATED_SKILLS[@]}"; do eric_skill="${s#* → }"; echo "   upstream-diffs/${eric_skill}.diff"; done
  echo "   查看方式：cat upstream-diffs/<skill>.diff"
  echo "   如要应用：手动把你认可的改动合入 skills/<skill>/SKILL.md"
  echo ""
fi

if [ ${#NEW_ATOMS[@]} -gt 0 ]; then
  echo "📚 已复制的新原子库文件："
  for f in "${NEW_ATOMS[@]}"; do echo "   知识库/原子库/$f"; done
  echo ""
fi

echo "接下来你需要做的："
echo "  1. 切换到新分支审查变更："
echo "     git checkout $BRANCH"
echo ""
echo "  2. 审查 diff 文件，手动合入你认可的上游改动"
echo ""
echo "  3. 确认无误后合并到 main："
echo "     git checkout main"
echo "     git merge $BRANCH"
echo "     git push origin main"
echo ""
echo "  4. 清理（合并后）："
echo "     git branch -d $BRANCH"
echo "     git push origin --delete $BRANCH"
echo "     rm -rf upstream-diffs/"
echo "=========================================="
