<div align="center">
  <img src="docs/assets/logo.png" alt="FAMQCORE" width="720">

  <h1>FAMQCORE Bot</h1>
  <p><strong>Discord-бот для заявок, рекрутинга, AFK-статусов и модерации игровых сообществ.</strong></p>

  [![CI](https://github.com/famqcore/Discordbot/actions/workflows/tests.yml/badge.svg)](https://github.com/famqcore/Discordbot/actions/workflows/tests.yml)
  [![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
  [![License: MIT](https://img.shields.io/badge/License-MIT-43a047.svg)](LICENSE)
</div>

**FAMQCORE Bot** автоматизирует путь участника от первой заявки до работы в сообществе: приватные тикеты, решения рекрутёров, централизованные логи, AFK-учёт и статистика. Бот изолирует данные между Discord-серверами и настраивается по ID ролей и каналов — переименование объектов не ломает работу.

> Проект находится в активной разработке. Используйте [Issues](https://github.com/famqcore/Discordbot/issues), чтобы сообщить о проблеме или предложить улучшение.

## Возможности

| Возможность | Что получает сообщество |
|---|---|
| 🎫 **Заявки и тикеты** | Две формы заявок, приватные каналы, история решений и защита от дубликатов. |
| 🛡️ **Рекрутинг** | Роли, действия «принять / отклонить», причины решений и вызов на обзвон. |
| 💤 **AFK-система** | Причина и время возврата, автоответ на упоминания, префикс ника и статистика. |
| 📊 **Статистика** | Сводка по заявкам, история тикетов и показатели AFK. |
| 🔒 **Изоляция данных** | Заявки и AFK-данные разделены по `guild_id`; пользовательские данные можно анонимизировать. |
| 🧰 **Эксплуатация** | Docker, SQLite-миграции, логи с ротацией, тесты, Ruff и GitHub Actions. |

## Быстрый старт

```bash
git clone https://github.com/famqcore/Discordbot.git
cd Discordbot/src/app
python -m pip install -r requirements.txt
cp .env.example .env
# Откройте .env и добавьте токен Discord-бота
python main.py
```

Перед запуском включите **Message Content Intent** и **Server Members Intent** в Discord Developer Portal. Полный пошаговый сценарий — в [руководстве по установке](docs/setup.md).

## Главные команды

| Команда | Назначение | Доступ |
|---|---|---|
| `!famqcore` | Открыть панель подачи заявки | участники сервера |
| `!afk` | Установить AFK-статус | участники сервера |
| `!afk_list` | Показать список AFK | участники сервера |
| `!stats` | Показать статистику заявок | администраторы |
| `!history` | Показать историю тикетов | администраторы |

Остальные команды, кнопки и права описаны в [документации](docs/commands.md).

## Документация

| Раздел | О чём |
|---|---|
| [Обзор](docs/overview.md) | Назначение, возможности и технологии |
| [Установка и запуск](docs/setup.md) | Настройка приложения Discord и первый запуск |
| [Конфигурация](docs/configuration.md) | Переменные окружения, роли, каналы и тексты |
| [Команды и кнопки](docs/commands.md) | Сценарии для участников и модераторов |
| [AFK-система](docs/afk.md) | Статусы, автоответы и статистика |
| [Архитектура](docs/architecture.md) | Модули, потоки данных и схема БД |
| [Деплой](docs/deployment.md) | Docker, логи, бэкапы и обновления |
| [Приватность](docs/privacy.md) | Хранимые данные и анонимизация |
| [Разработка](docs/contributing.md) | Тесты, стиль кода и Pull Request |
| [Дорожная карта](docs/roadmap.md) | Приоритеты развития |

## Участие

Нашли ошибку, хотите предложить функцию или улучшить документацию? Откройте [Issue](https://github.com/famqcore/Discordbot/issues) или создайте Pull Request. Перед PR прочитайте [CONTRIBUTING.md](CONTRIBUTING.md) и выполните локальные проверки.

## Лицензия

Код распространяется по лицензии [MIT](LICENSE).
