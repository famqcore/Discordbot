<div align="center">
  <h1>FAMQCORE Bot</h1>
  <p><strong>Discord-бот для приёма заявок в семью и учёта AFK. Сделан для владельцев семей на Majestic RP и GTA 5 RP.</strong></p>

  <img src="docs/assets/logo.png" alt="FAMQCORE" width="720">

  [![CI](https://github.com/famqcore/famqcore_discord/actions/workflows/tests.yml/badge.svg)](https://github.com/famqcore/famqcore_discord/actions/workflows/tests.yml)
  [![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
  [![License: MIT](https://img.shields.io/badge/License-MIT-43a047.svg)](LICENSE)
</div>

Бот закрывает весь путь новичка: человек жмёт кнопку, заполняет анкету, получает приватный тикет, рекрутёр принимает или отказывает, всё решение уходит в приватный лог-канал. Плюс отдельная AFK-система: статус с причиной, автоответ на упоминания и статистика отсутствий.

Данные разных Discord-серверов не смешиваются, а все роли и каналы настраиваются по ID, так что переименование канала или роли ничего не ломает.

> Проект в активной разработке. Нашли баг или хотите фичу - напишите в [Issues](https://github.com/famqcore/famqcore_discord/issues).

## Что умеет

| Возможность | Что это даёт |
|---|---|
| Заявки и тикеты | Две формы (RP и CAPT), приватный канал под каждую заявку, защита от второй заявки от одного человека |
| Рекрутинг | Кнопки «Принять», «Отказать», «Вызвать на обзвон», «Закрыть тикет» с обязательной причиной решения |
| Лог-центр | Один приватный канал с ветками: заявки, решения, AFK, обзвоны, статистика, ошибки, аудит |
| AFK | Причина, время возврата, префикс `[AFK]` в нике, автоответ на упоминание, личная статистика |
| Статистика | Сводка по заявкам за всё время и по дням, история последних тикетов |
| Приватность | Разделение по `guild_id`, удаление данных участника одной командой, автоочистка старых заявок |
| Эксплуатация | Docker без root, миграции SQLite, ротация логов, 650+ тестов, Ruff и GitHub Actions |

## Быстрый старт

```bash
git clone https://github.com/famqcore/famqcore_discord.git
cd famqcore_discord/src/app
python -m pip install --require-hashes -r requirements.txt
cp .env.example .env
# впишите в .env токен бота
python main.py
```

До запуска включите в Discord Developer Portal два интента: **Message Content Intent** и **Server Members Intent**. Без них команды с префиксом `!` работать не будут. Полная пошаговая инструкция лежит в [docs/setup.md](docs/setup.md).

## Основные команды

| Команда | Что делает | Кто может |
|---|---|---|
| `!famqcore` | Открывает панель подачи заявки | все |
| `!afk` | Меню своего AFK-статуса | все |
| `!afk_list` | Кто сейчас в AFK | все |
| `!afk_check @user` | Статус конкретного человека | все |
| `!afk_stats @user` | Статистика AFK человека | все |
| `!afk_remove @user` | Снять AFK принудительно | модераторы |
| `!stats` | Статистика заявок | админы Discord |
| `!history [N]` | Последние N заявок (1-25, по умолчанию 10) | админы Discord |
| `!delete_user_data @user` | Удалить данные участника | админы Discord |

Подробности по кнопкам и правам - в [docs/commands.md](docs/commands.md).

## Документация

| Раздел | О чём |
|---|---|
| [Обзор](docs/overview.md) | Зачем бот нужен и из чего состоит |
| [Установка и запуск](docs/setup.md) | Приложение в Discord, токен, права, первый старт |
| [Конфигурация](docs/configuration.md) | Переменные `.env`, настройки и тексты в `config.py` |
| [Команды и кнопки](docs/commands.md) | Сценарии участника и модератора |
| [AFK-система](docs/afk.md) | Статусы, форматы времени, автоответы, статистика |
| [Архитектура](docs/architecture.md) | Модули, потоки данных, схема БД |
| [Деплой](docs/deployment.md) | Docker, обновления, бэкапы, диагностика |
| [Приватность](docs/privacy.md) | Что хранится, как удаляется, сроки хранения |
| [Тесты и линтер](docs/testing.md) | Как прогнать проверки локально |
| [Разработка](docs/contributing.md) | Процесс работы над задачей и PR |
| [Скрипты обслуживания](docs/scripts.md) | Бэкап базы, очистка кеша, обновление из GitHub |
| [Дорожная карта](docs/roadmap.md) | Что планируем дальше |

## Как помочь

Откройте [Issue](https://github.com/famqcore/famqcore_discord/issues) с багом или идеей либо присылайте Pull Request. Перед PR загляните в [CONTRIBUTING.md](CONTRIBUTING.md) и прогоните локальные проверки.

## Лицензия

[MIT](LICENSE).
