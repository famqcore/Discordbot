#!/usr/bin/env bash
# Бэкап базы данных бота.
#
# Копия снимается онлайн через sqlite3 .backup, поэтому бота останавливать
# не нужно: режим WAL и параллельная запись бэкапу не мешают.
#
# Использование:
#   ./scripts/backup.sh                  # копия в backups/, хранить 14 копий
#   ./scripts/backup.sh /mnt/disk 30     # своя папка и своё число копий

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${DB_PATH:-$ROOT/src/app/database/database.db}"
BACKUP_DIR="${1:-${BACKUP_DIR:-$ROOT/backups}}"
KEEP="${2:-${BACKUP_KEEP:-14}}"

if [ ! -f "$DB_PATH" ]; then
    echo "Базы нет: $DB_PATH" >&2
    echo "Похоже, бот ещё ни разу не запускался." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/database-$STAMP.db"

if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$TARGET'"
else
    python3 - "$DB_PATH" "$TARGET" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY
fi

if command -v gzip >/dev/null 2>&1; then
    gzip -f "$TARGET"
    TARGET="$TARGET.gz"
fi

echo "Готово: $TARGET"

# Прунинг: оставляем KEEP самых свежих копий.
if [ "$KEEP" -gt 0 ]; then
    ls -1t "$BACKUP_DIR"/database-*.db* 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
        rm -f "$old"
        echo "Удалил старую копию: $old"
    done
fi
