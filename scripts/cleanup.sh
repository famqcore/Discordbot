#!/usr/bin/env bash
# Очистка временных файлов проекта.
#
# Удаляет кеши Python и тестов, отчёты покрытия и подрезает старые логи.
# Базу данных и .env не трогает.
#
# Использование:
#   ./scripts/cleanup.sh          # обычная очистка
#   ./scripts/cleanup.sh --logs   # плюс удалить ротационные файлы логов

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_LOGS=0
if [ "${1:-}" = "--logs" ]; then
    WITH_LOGS=1
fi

echo "Чищу кеши Python..."
find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov
rm -f .coverage .coverage.*

if [ "$WITH_LOGS" -eq 1 ]; then
    echo "Удаляю старые файлы логов (bot.log остаётся)..."
    find src/app/logs -type f -name "bot.log.*" -delete 2>/dev/null || true
fi

DB_PATH="${DB_PATH:-$ROOT/src/app/database/database.db}"
if [ -f "$DB_PATH" ]; then
    echo "Сбрасываю WAL и сжимаю базу..."
    python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.execute("VACUUM")
conn.close()
PY
fi

echo "Готово."
