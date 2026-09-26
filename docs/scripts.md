# Скрипты обслуживания

В папке `scripts/` лежат три задачи: бэкап базы, очистка временных файлов и обновление бота из GitHub. Для каждой есть два файла: `.sh` для Linux и macOS, `.ps1` для Windows. Делают они одно и то же, просто на разных системах.

Запускать нужно из корня репозитория. На Linux скрипты уже исполняемые, если нет: `chmod +x scripts/*.sh`.

## Бэкап базы

```bash
./scripts/backup.sh                 # копия в backups/, хранится 14 последних
./scripts/backup.sh /mnt/disk 30    # своя папка и своё число копий
```

```powershell
.\scripts\backup.ps1
.\scripts\backup.ps1 -BackupDir D:\backups -Keep 30
```

Копия снимается онлайн через backup API SQLite, поэтому бота останавливать не нужно: параллельная запись и режим WAL копии не мешают. Файл называется `database-ГГГГММДД-ЧЧММСС.db` и сжимается (`gzip` на Linux, zip на Windows). Копии старше лимита удаляются, чтобы папка не росла бесконечно. Папка `backups/` в `.gitignore`, в репозиторий бэкапы не попадают.

Восстановление простое: остановите бота, распакуйте нужную копию и положите её на место `src/app/database/database.db`.

## Очистка временных файлов

```bash
./scripts/cleanup.sh          # кеши и отчёты покрытия
./scripts/cleanup.sh --logs   # плюс старые ротационные файлы логов
```

```powershell
.\scripts\cleanup.ps1
.\scripts\cleanup.ps1 -WithLogs
```

Что удаляется: папки `__pycache__`, файлы `.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `htmlcov`, файлы `.coverage*`. С флагом логов убираются ротационные файлы `bot.log.*`, текущий `bot.log` остаётся.

Заодно скрипт сбрасывает журнал WAL и выполняет `VACUUM`: база ужимается, файлы `database.db-wal` и `database.db-shm` схлопываются. Сами данные, `.env` и бэкапы не трогаются.

## Обновление из GitHub

```bash
./scripts/update.sh          # обновиться из текущей ветки
./scripts/update.sh main     # обновиться из ветки main
```

```powershell
.\scripts\update.ps1
.\scripts\update.ps1 -Branch main
```

Порядок такой: проверка, что в рабочей копии нет незакоммиченных правок (иначе скрипт останавливается и ничего не делает), бэкап базы, `git fetch` и `git pull --ff-only`, установка зависимостей из `src/app/requirements.txt` с проверкой хешей. В конце печатается список новых коммитов и напоминание перезапустить бота:

```bash
sudo systemctl restart famqcore-bot   # systemd
docker compose up -d --build          # docker
```

Если используется виртуальное окружение `.venv` в корне репозитория, скрипт возьмёт python именно из него.

## Расписание

Бэкап и очистку удобно повесить на планировщик. Пример для cron (ежедневный бэкап в 4 утра, очистка по воскресеньям):

```
0 4 * * * cd /opt/famqcore_discord && ./scripts/backup.sh >> /var/log/famqcore-backup.log 2>&1
30 4 * * 0 cd /opt/famqcore_discord && ./scripts/cleanup.sh >> /var/log/famqcore-cleanup.log 2>&1
```

На Windows то же самое делается через Планировщик заданий: действие `powershell.exe -ExecutionPolicy Bypass -File C:\famqcore_discord\scripts\backup.ps1`.
