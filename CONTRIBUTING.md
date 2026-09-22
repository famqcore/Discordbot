# Как поучаствовать

Спасибо за интерес к проекту! Мы в бете и рады любой помощи. Полная версия для желающих помочь - [docs/contributing.md](docs/contributing.md), тут кратко процесс.

## Сообщить об ошибке

1. Проверьте, нет ли уже похожего Issue в [трекере](https://github.com/yyangdev/majestick-famq-discord-bot/issues).
2. Если нет - создайте новый с описанием:
   - что ожидали,
   - что произошло,
   - шаги для воспроизведения,
   - кусок лога из консоли или `logs/bot.log`.

## Предложить улучшение

Создайте Issue и опишите: зачем нужно, как должно работать, пример использования.

## Pull Request

1. Форкните репозиторий.
2. Ветка: `git checkout -b feature/название` или `fix/номер-issue`.
3. Изменения + тесты на новое поведение.
4. Проверки перед отправкой:
   ```bash
   cd src/app
   TOKEN=dummy python -m unittest discover -s tests -v
   ruff check .
   ruff format --check .
   ```
5. Коммит, пуш, Pull Request с описанием и ссылкой на issue.

## Требования к коду

- Минималистичный стиль, PEP8.
- Все тексты и настройки - только в `config.py`.
- Новый функционал с тестами, старые тесты не ломать.
- `ruff check .` и `ruff format --check .` зелёные.

## Контакты

- Discord: **yangblya**
- Telegram: **[@yyangov](https://t.me/yyangov)**
