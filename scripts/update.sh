#!/usr/bin/env bash
# Обновление бота из GitHub.
#
# Порядок: бэкап базы -> git pull -> установка зависимостей -> подсказка
# про перезапуск. Если в рабочей копии есть незакоммиченные правки,
# скрипт останавливается и ничего не делает.
#
# Использование:
#   ./scripts/update.sh            # обновиться из текущей ветки
#   ./scripts/update.sh main       # обновиться из ветки main

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v git >/dev/null 2>&1; then
    echo "git не найден в PATH." >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "В рабочей копии есть незакоммиченные изменения." >&2
    echo "Сохраните их (git stash или git commit) и запустите скрипт снова." >&2
    exit 1
fi

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

echo "Делаю бэкап базы перед обновлением..."
"$ROOT/scripts/backup.sh" || echo "Бэкап пропущен (базы ещё нет)."

echo "Забираю изменения из origin/$BRANCH..."
BEFORE="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "Обновлений нет, версия уже последняя."
else
    echo "Обновлено: $BEFORE -> $AFTER"
    git --no-pager log --oneline "$BEFORE..$AFTER" | head -20
fi

PYTHON="${PYTHON:-python3}"
if [ -d "$ROOT/.venv" ]; then
    PYTHON="$ROOT/.venv/bin/python"
fi

echo "Ставлю зависимости..."
"$PYTHON" -m pip install --disable-pip-version-check --require-hashes \
    -r src/app/requirements.txt

echo
echo "Готово. Перезапустите бота:"
echo "  systemd:  sudo systemctl restart famqcore-bot"
echo "  docker:   docker compose up -d --build"
