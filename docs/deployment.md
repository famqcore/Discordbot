# Деплой

## Docker Compose (рекомендуемый способ)

```bash
cp src/app/.env.example src/app/.env
# заполните TOKEN и нужные ID

docker compose up -d --build
docker compose logs -f
```

Контейнер собран так, чтобы в случае чего он мог сделать как можно меньше:

- процесс работает от пользователя `bot:bot` (UID и GID 10001), не от root;
- корневая файловая система только для чтения;
- все Linux capabilities сброшены, включён `no-new-privileges`;
- `/tmp` это маленький tmpfs с `noexec,nosuid`;
- писать можно только в тома `/app/database` и `/app/logs`;
- количество процессов ограничено (`pids_limit: 100`).

Сеть не отключаем: боту нужны исходящие HTTPS и WebSocket к Discord API.

Повседневные команды:

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose up -d --build
```

### Если тома остались от старой версии

Раньше контейнер работал от root. Перед первым запуском новой версии один раз передайте данные пользователю 10001:

```bash
docker compose down
docker compose run --rm --user root --entrypoint sh bot \
  -c 'chown -R 10001:10001 /app/database /app/logs'
docker compose up -d
```

Для bind mount сделайте такой же `chown` на хосте. Не ставьте права 777: в базе и логах персональные данные.

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

Без томов база и логи пропадут вместе с контейнером.

## Воспроизводимая сборка

Базовый образ зафиксирован полным тегом и digest:

```dockerfile
FROM python:3.11.16-slim-bookworm@sha256:a36c24f...
```

`requirements.txt` содержит точные версии всех зависимостей, включая транзитивные, и SHA-256 каждого файла. Установка идёт с `pip --require-hashes`, поэтому подменить пакет молча не получится: сборка просто упадёт.

Ветка Python зафиксирована на 3.11 не просто так: lock-файлы собраны под cp311. Переход на другую версию Python это перегенерация обоих lock-файлов плюс правка `python-version` в `.github/workflows/tests.yml`.

### Обновление Python-зависимостей

Dependabot раз в неделю открывает сгруппированные PR. Руками это делается так:

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

В PR приложите diff обоих lock-файлов, результат тестов и вывод `pip-audit`. `pip freeze` не используем: прямые зависимости живут в `.in`, транзитивные вычисляет `pip-compile`.

### Обновление базового образа

1. Выберите поддерживаемый полный тег `python:3.11.x-slim-bookworm`.
2. Возьмите multi-platform digest через `docker buildx imagetools inspect` или Docker Hub API.
3. Одновременно поменяйте тег и `sha256:` в `Dockerfile`.
4. Соберите образ и дождитесь CI: проверка non-root пользователя, Grype и SBOM обязательны.

Dependabot следит за digest образа и версиями GitHub Actions. Все Actions в workflow закреплены по полному commit SHA.

## Проверки безопасности в CI

Job `Dependency and container security` запускается на каждый push и PR и делает пять вещей:

1. ставит dev-инструменты с хешами;
2. гоняет `pip-audit --strict` по runtime-зависимостям, любой известный advisory ломает сборку;
3. собирает production-образ и проверяет, что его пользователь именно `bot:bot`;
4. формирует SPDX SBOM и кладёт его артефактом `production-sbom-spdx` на 30 дней;
5. сканирует образ через Grype и падает на уязвимостях уровня critical, для которых уже есть исправление.

Опция `only-fixed` делает такой gate рабочим: CVE без доступного фикса не блокирует сборку, но как только фикс выходит, следующий CI обязан на него перейти. Порог сканера меняется только отдельным ревью с объяснением. Исключения для CVE должны иметь номер, владельца, причину и срок, бессрочных исключений не делаем.

## Данные и миграции

| Путь | Что там |
|---|---|
| `/app/database/database.db` | SQLite со всеми таблицами и `bot_state` |
| `/app/logs/bot.log` | Лог с ротацией, 3 архива по 5 МБ |

Версия схемы хранится в `PRAGMA user_version`, недостающие миграции применяются при старте, перед миграцией бот делает бэкап файла базы. Перед обновлением на проде всё равно сделайте свою копию:

```bash
docker compose exec bot cp /app/database/database.db \
  /app/database/database-backup-$(date +%F).db
```

Бэкап это копия персональных данных. Ограничьте к нему доступ и настройте срок хранения (разумно держать 14 ежедневных копий). Команда `!delete_user_data` чистит рабочую базу и лог-центр, а из бэкапов данные уходят по мере их ротации, подробности в [privacy.md](privacy.md).

Восстановление: остановить бота, положить файл на место как `database.db`, проверить владельца `10001:10001`, запустить контейнер. Вместе с базой вернутся `bot_state` и связи тикетов с сообщениями лог-центра.

## Диагностика

При старте бот проверяет конфигурацию и токен, а после подключения инфраструктуру серверов. Фатальная ошибка завершает процесс с ненулевым кодом, так что `restart: unless-stopped` не будет вечно поднимать заведомо сломанную конфигурацию незамеченной. Смотрите оба потока логов:

```bash
docker compose logs --tail=200 bot
docker compose exec bot tail -200 /app/logs/bot.log
```

Не выкладывайте в Issue токены, ID приватных каналов, транскрипты и персональные данные участников.
