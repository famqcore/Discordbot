# Деплой

## Docker Compose (рекомендуемый способ)

```bash
cp src/app/.env.example src/app/.env
# заполните TOKEN и ID

docker compose up -d --build
docker compose logs -f
```

Контейнер работает с минимальными привилегиями:

- процесс запускается как `bot:bot` (UID/GID `10001`), не root;
- root filesystem доступна только для чтения;
- все Linux capabilities удалены, включён `no-new-privileges`;
- `/tmp` — ограниченный `tmpfs` с `noexec,nosuid`;
- запись разрешена только в volumes `/app/database` и `/app/logs`;
- число процессов ограничено в Compose.

Исходящие HTTPS/WebSocket-соединения нужны для Discord API, поэтому сеть контейнера не отключается.

Управление:

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose up -d --build
```

### Обновление существующих root-owned volumes

Старые версии запускались от root. Перед первым запуском новой версии один раз передайте существующие данные UID/GID 10001:

```bash
docker compose down
docker compose run --rm --user root --entrypoint sh bot \
  -c 'chown -R 10001:10001 /app/database /app/logs'
docker compose up -d
```

Для bind mounts выполните эквивалентный `chown` на хосте. Не меняйте владельца на `777`: база и логи содержат персональные данные.

## Голый Docker

```bash
docker build -t famqcore-bot .
docker run -d --name famqcore-bot \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env-file src/app/.env \
  -v famqcore-data:/app/database -v famqcore-logs:/app/logs \
  famqcore-bot
```

Без volumes база и файловые логи не переживут замену контейнера.

## Воспроизводимая поставка

Production-образ основан на полном теге Python и multi-platform digest:

```dockerfile
FROM python:3.11.16-slim-bookworm@sha256:a36c24f...
```

`requirements.txt` содержит точные версии всех транзитивных зависимостей и SHA-256 каждого разрешённого артефакта. Docker устанавливает его с `pip --require-hashes`; изменение пакета без обновления lock-файла ломает сборку, а не подменяет зависимость молча.

### Контролируемое обновление Python-зависимостей

Dependabot раз в неделю открывает сгруппированные PR. Для ручного обновления:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install 'pip-tools==7.6.1'

pip-compile --upgrade --generate-hashes --strip-extras \
  --index-url=https://pypi.org/simple \
  --output-file=src/app/requirements.txt src/app/requirements.in
pip-compile --upgrade --generate-hashes --strip-extras --allow-unsafe \
  --index-url=https://pypi.org/simple \
  --output-file=src/app/requirements-dev.txt src/app/requirements-dev.in
```

В одном PR приложите diff обоих lock-файлов, результаты тестов и `pip-audit`. Не используйте `pip freeze`: входные зависимости должны оставаться в `.in`, транзитивные — вычисляться `pip-compile`.

### Обновление базового образа

1. Выберите поддерживаемый полный тег `python:3.11.x-slim-bookworm`.
2. Получите multi-platform digest через `docker buildx imagetools inspect` или Docker Hub API.
3. Одновременно измените тег и `sha256:` в `Dockerfile`.
4. Соберите образ и дождитесь CI: non-root assertion, Grype и SBOM обязательны.

Dependabot еженедельно следит за Docker digest и GitHub Actions. Все Actions в workflow закреплены полными commit SHA.

## Security gates и SBOM

Job `Dependency and container security` на каждом push/PR:

1. устанавливает hash-locked dev tooling;
2. запускает строгий `pip-audit` для runtime lock (любой известный Python advisory ломает CI);
3. собирает production-образ и доказывает, что его runtime user — `bot:bot`;
4. формирует SPDX JSON SBOM и публикует artifact `production-sbom-spdx` на 30 дней;
5. сканирует весь образ Grype и ломает CI на уязвимостях уровня **critical**, для которых опубликовано исправление.

`only-fixed` делает gate исполнимым: CVE без доступной версии исправления не блокирует сборку; после появления fix следующий CI обязан перейти на него. Полный состав образа остаётся в SBOM для внешнего мониторинга. Порог контейнерного сканера изменяется только отдельным review с объяснением. Исключения CVE должны иметь идентификатор, владельца, обоснование и срок; бессрочные allowlist запрещены.

## Данные и миграции

| Путь | Содержимое |
|---|---|
| `/app/database/database.db` | SQLite и `bot_state` |
| `/app/logs/bot.log` | Ротируемый лог |

Схема управляется `PRAGMA user_version`; недостающие миграции применяются при старте. Перед production-обновлением сделайте бэкап:

```bash
docker compose exec bot cp /app/database/database.db \
  /app/database/database-backup-$(date +%F).db
```

Бэкапы содержат персональные данные. Ограничьте доступ и настройте срок хранения (рекомендация — 14 ежедневных копий). Команда `!delete_user_data` очищает рабочую БД и лог-центр; копии исчезают по мере ротации, как описано в [privacy.md](privacy.md).

Восстановление: остановить бот, вернуть файл как `database.db`, проверить владельца `10001:10001`, запустить контейнер. Вместе с БД восстанавливаются `bot_state` и связи тикетов с лог-сообщениями.

## Диагностика

При старте бот проверяет конфигурацию и TOKEN; фатальная ошибка завершает процесс с ненулевым кодом. Смотрите оба потока:

```bash
docker compose logs --tail=200 bot
docker compose exec bot tail -200 /app/logs/bot.log
```

Не публикуйте токены, ID приватных каналов, транскрипты или персональные данные в Issue.
