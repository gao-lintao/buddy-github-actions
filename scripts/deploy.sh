#!/usr/bin/env bash
# deploy.sh —— 把 buddy-github-actions 一键推送到 GitHub
# 用法：REPO=你的用户名/你的仓库 bash scripts/deploy.sh
# 仅负责 git 部分（init/add/commit/remote/push），不含任何凭据；
# 首次 push 请用 GitHub 个人访问令牌(PAT) 作为密码（GitHub 已不支持账户密码）。
set -euo pipefail

REPO="${REPO:-}"
if [ -z "$REPO" ]; then
  echo "用法：REPO=owner/repo bash scripts/deploy.sh" >&2
  exit 1
fi

cd "$(dirname "$0")/.." || exit 1

# 1) git 身份检查（仅首次需要）
if [ -z "$(git config user.name 2>/dev/null)" ] || [ -z "$(git config user.email 2>/dev/null)" ]; then
  echo "本机尚未配置 git 身份，请先执行一次：" >&2
  echo "  git config --global user.name  '你的名字'" >&2
  echo "  git config --global user.email '你的邮箱@example.com'" >&2
  exit 1
fi

# 2) 初始化（若还不是仓库）
if [ ! -d .git ]; then
  git init -q
  echo "已初始化 git 仓库"
fi

# 3) 取当前分支名（兼容 main / master / 未出生分支）
BR="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"

# 4) 暂存（受 .gitignore 约束，令牌文件不会入库）
git add -A
if git diff --cached --quiet; then
  echo "没有需要提交的新改动，跳到推送。"
else
  git commit -q -m 'chore: init Buddy 加油站 GitHub Actions 方案'
  echo "已提交改动"
fi

# 5) 关联远程
REMOTE="https://github.com/${REPO}.git"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE"
else
  git remote add origin "$REMOTE"
fi
echo "远程 origin → $REMOTE"

# 6) 推送（认证请用 PAT 作为密码）
echo "开始推送……首次推送请用 GitHub 个人访问令牌(PAT) 作为密码。"
git push -u origin "$BR"

echo "完成 ✅ 已推送到 https://github.com/${REPO}"
echo "接下来：在 GitHub 网页添加 Secret（见 README 第二节或 refresh_secret.sh 输出）。"
