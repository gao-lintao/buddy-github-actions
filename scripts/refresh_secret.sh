#!/usr/bin/env bash
# =============================================================================
# refresh_secret.sh —— 本机一次性刷新脚本
# 作用：读取本机 WorkBuddy 登录态 → 解密导出接口令牌(access token) → 写入 GitHub 仓库 Secret。
#       之后云端 Actions 直接把它当作 Bearer 令牌使用，无需本机常开、无需把令牌写进仓库文件。
#
# 说明：接口令牌有效期约 28 天，建议每 3~4 周重跑一次本脚本覆盖 Secret；
#       若某天运行报「令牌失效」，重跑本脚本即可。Secret 在 GitHub 中以密文保存，绝不以明文入仓/入日志。
#
# 前置：
#   1) 本机已登录 WorkBuddy 客户端
#   2) 本机已安装「WorkBuddy积分助手」技能（提供 buddy_station.py）
#   3) gh CLI 已登录（gh auth login）
#
# 用法：
#   REPO=你的用户名/你的仓库  bash scripts/refresh_secret.sh
#   可选：设置 WORKBUDDY_SKILL_DIR 指定技能 scripts 目录（默认 ~/.workbuddy/.../scripts）
# =============================================================================
set -euo pipefail

REPO="${REPO:-}"   # 例如：gaolintao/buddy-github-actions
if [ -z "$REPO" ]; then
  echo "请先设置仓库：REPO=owner/repo bash scripts/refresh_secret.sh" >&2
  exit 1
fi

SKILL_DIR="${WORKBUDDY_SKILL_DIR:-$HOME/.workbuddy/skills/totorosir-workbuddy-score/scripts}"
if [ ! -f "$SKILL_DIR/buddy_station.py" ]; then
  echo "找不到 buddy_station.py，请通过 WORKBUDDY_SKILL_DIR 指定技能 scripts 目录。" >&2
  exit 1
fi

PYTHON="$(command -v python3)"

echo "[1/2] 正在从本机登录态解密导出接口令牌……"
# 复用技能的通道 A 鉴权（已在本机验证可用），解密后的明文令牌打印到 stdout
TOKEN="$($PYTHON - "$SKILL_DIR" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
import buddy_station as bs
t, _, _ = bs._resolve_auth("auto")
print(t)
PY
)"
TOKEN="$(echo "$TOKEN" | tr -d '[:space:]')"
if [ -z "$TOKEN" ]; then
  echo "导出失败：请确认 WorkBuddy 客户端已登录，且技能路径正确。" >&2
  exit 1
fi

if command -v gh >/dev/null 2>&1; then
  echo "[2/2] 检测到 gh CLI，正在写入仓库 secret WORKBUDDY_TOKEN → $REPO"
  gh secret set WORKBUDDY_TOKEN --repo "$REPO" --body "$TOKEN"
  echo "完成 ✅ 接口令牌已通过 gh 写入 Secret。"
else
  # 本机未安装 gh：令牌落本地 .env.local（已被 .gitignore 忽略），不打印明文，不入库
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  ENV_FILE="$ROOT/.env.local"
  printf 'WORKBUDDY_TOKEN=%s\n' "$TOKEN" > "$ENV_FILE"
  echo "[2/2] 未检测到 gh CLI，已将令牌导出到本地文件（不会打印明文，也不会入库）："
  echo "      $ENV_FILE"
  echo
  echo "请在 GitHub 网页手动添加 Secret："
  echo "  1) 打开 https://github.com/$REPO/settings/secrets/actions"
  echo "  2) 点击 『New repository secret』"
  echo "  3) Name 填 WORKBUDDY_TOKEN；Value 执行下面命令复制后粘贴："
  echo "       sed 's/^WORKBUDDY_TOKEN=//' \"$ENV_FILE\""
  echo
  echo "⚠️ 添加完成后建议删除该本地文件以彻底清除明文令牌：rm \"$ENV_FILE\""
fi
