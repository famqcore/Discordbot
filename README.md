# Regent FamQ Bot

[![CI](https://github.com/yyangdev/majestick-famq-discord-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/yyangdev/majestick-famq-discord-bot/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Discord-бот для игровой семьи Regent на проекте Majestic RP. Принимает заявки на вступление, ведёт учёт AFK-статусов и статистику по рекрутингу.

> **Проект в beta.** Основные сценарии работают, но до мульти-серверного использования необходимо завершить изоляцию данных и миграции. Актуальные блокеры собраны в [epic #108](https://github.com/yyangdev/majestick-famq-discord-bot/issues/108).

## Что умеет бот

- Тикет-система с двумя типами заявок: RP и CAPT
- AFK-система: статус с причиной и временем возврата, автоответ на упоминания и статистика
- Команды `!stats` и `!history` для администраторов; в будущем их заменит веб-панель

## Быстрый старт

```bash
git clone https://github.com/yyangdev/majestick-famq-discord-bot.git
cd majestick-famq-discord-bot/src/app
pip install -r requirements.txt
cp .env.example .env   # вписать токен бота
python main.py
```

Полная инструкция, включая настройку сервера Discord и прав бота: [docs/setup.md](docs/setup.md).

## Документация

| Раздел | О чём |
|--------|-------|
| [Обзор проекта](docs/overview.md) | Для чего бот, статус разработки, планы |
| [Установка и запуск](docs/setup.md) | Пошаговый запуск с нуля |
| [Настройка](docs/configuration.md) | Все переменные config.py и .env |
| [Команды и кнопки](docs/commands.md) | Что и кто может нажимать |
| [AFK-система](docs/afk.md) | Как работает AFK-учёт |
| [Архитектура](docs/architecture.md) | Устройство кода и база данных |
| [Деплой](docs/deployment.md) | Docker, бэкапы, обновления |
| [Приватность и данные](docs/privacy.md) | Что хранится, кто имеет доступ, как удалить данные |
| [Тесты и линтер](docs/testing.md) | Как гонять проверки перед PR |
| [Как помочь проекту](docs/contributing.md) | С чего начать новичку в проекте |
| [Дорожная карта](docs/roadmap.md) | Что будем делать дальше |

## Как помочь

Проект в начале пути, любая помощь в цене: от отчёта о баге до готового Pull Request. Список задач - в [Issues](https://github.com/yyangdev/majestick-famq-discord-bot/issues), там есть простые для старта. Подробности в [docs/contributing.md](docs/contributing.md) и [CONTRIBUTING.md](CONTRIBUTING.md).

## Контакты

- Discord: **yangblya**
- Telegram: **[@yyangov](https://t.me/yyangov)**

## Лицензия

MIT. Подробности в [LICENSE](LICENSE).
