#!/usr/bin/env bash
# T8.1 存量清理：去 Git 本地化改造的旧数据迁移（一次性）。
#
# 背景：T8.1 起 Project 模型去掉 git 字段（repo_url/encrypted_token/branch/
# last_sync_at/sync_error）、GitTask 表删除，项目内容改存 PROJECTS_DIR。
# 旧 DB 记录与 /data/repos clone 目录不可映射到新架构——备份后清除，
# 用户表也随之重置（需重新 user-add；备份可整包回滚）。
#
# 用法（platform/ 目录下）：
#   bash scripts/migrate-t8.1.sh              # 默认 DATA_DIR=platform/data
#   bash scripts/migrate-t8.1.sh /data        # compose 部署卷路径
set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${1:-${DATA_DIR:-$PLATFORM_DIR/data}}"

if [ ! -d "$DATA_DIR" ]; then
  echo "[migrate] 数据目录不存在（$DATA_DIR）——全新环境，无需清理"
  exit 0
fi

cd "$PLATFORM_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="data-backup-$TS.tar.gz"

echo "[migrate] 1/3 备份 $DATA_DIR → $BACKUP"
tar -czf "$BACKUP" -C "$DATA_DIR" .

echo "[migrate] 2/3 清除 git 时代遗留"
rm -rf "$DATA_DIR/repos"                      # clone 目录（新架构由上传解压替代）
rm -f "$DATA_DIR/platform.db" \
      "$DATA_DIR/platform.db-wal" \
      "$DATA_DIR/platform.db-shm"             # 旧 schema DB（init_tables 重建）
rm -rf "$DATA_DIR/shots"                      # 临时截图区

echo "[migrate] 3/3 建新目录骨架"
mkdir -p "$DATA_DIR/projects"

cat <<EOF
[migrate] 完成。
- 备份：${PLATFORM_DIR}/${BACKUP} （回滚：tar -xzf 解回 ${DATA_DIR}）
- 旧项目绑定的是 git 仓库，无法迁入本地目录；需新建项目并重新上传内容
- 用户表已重置，请重新添加：python -m server.cli user-add <email> <name>
EOF
