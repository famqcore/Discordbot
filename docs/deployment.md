# Деплой

Запуск в бою и всё, что с этим связано: Docker, данные, бэкапы, обновления.

## Docker Compose (рекомендуемый способ)

```bash
cp src/app/.env.example src/app/.env   # вписать токен
docker compose up -d
```

Compose-файл маленький:

- один сервис `bot` с `restart: unless-stopped`,
- `.env` берётся из `src/app/.env`,
- база лежит в volume `bot_data`, логи - в `bot_logs`.

Управление:

```bash
docker compose logs -f      # смотреть логи
docker compose restart      # перезапуск
docker compose down         # остановить
docker compose up -d --build   # пересобрать и запустить
```

## Голый Docker

```bash
docker build -t regent-bot .
docker run -d --name regent-bot --env-file src/app/.env regent-bot
```

Без volumes данные умрут вместе с контейнером, так что для прода лучше compose.

## Где что лежит внутри контейнера

| Путь | Что |
|------|-----|
| `/app` | Код |
| `/app/database/database.db` | База SQLite (volume) |
| `/app/logs/bot.log` | Логи (volume) |

## Важно знать про текущий Dockerfile

Честный список, все пункты заведены в трекере:

- Контейнер работает от root - issue #45.
- При старте бот валидирует конфигурацию и наличие `TOKEN`; без корректного `.env` процесс завершится с ошибкой.
- Healthcheck отсутствует, `unhealthy` контейнер не отловить автоматом, #45.
- Тесты в production-контейнере не запускаются: их выполняет CI до деплоя.

## Обновление версии

```bash
git pull
docker compose up -d --build
```

Схема базы управляется встроенными SQLite-миграциями через `PRAGMA user_version`. При старте бот применяет недостающие миграции автоматически; перед обновлением production всё равно сделайте бэкап `database.db`.

## Бэкап вручную

База - один файл, бэкап простой:

```bash
docker compose exec bot cp /app/database/database.db /app/database/database-backup-$(date +%F).db
```

Файл окажется в volume `bot_data`, оттуда его можно забрать на хост. Автоматика и политика хранения - task #98. До её закрытия советуем делать копию раз в неделю руками.

Восстановление: остановить бота, положить файл обратно как `database.db`, запустить.

## Логи

Два потока: `docker compose logs` и файл `logs/bot.log` (ротация 3x5 МБ). При странном поведении бота первым делом смотрите оба.

Если что-то совсем развалилось - пишите с куском лога: Discord **yangblya**, Telegram [@yyangov](https://t.me/yyangov).
