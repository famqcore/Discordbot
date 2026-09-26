# Участие в FAMQCORE Bot

Спасибо за интерес к проекту. Помочь можно кодом, тестированием на живом сервере, документацией или просто хорошей идеей.

## Сообщить об ошибке

1. Посмотрите, нет ли такой же задачи в [Issues](https://github.com/famqcore/famqcore_discord/issues).
2. Если нет, заведите новую и напишите:
   - что ожидали и что получили;
   - шаги, чтобы повторить;
   - версию Python, ОС и версию discord.py;
   - безопасный кусок лога из консоли или `src/app/logs/bot.log`.

Не выкладывайте токен бота, ID приватных каналов и персональные данные участников.

## Предложить улучшение

Заведите [Issue](https://github.com/famqcore/famqcore_discord/issues) и ответьте на три вопроса: какую проблему решаем, как это должно выглядеть для пользователя и как проверить, что получилось.

## Pull Request

1. Сделайте форк и ветку: `feature/название` или `fix/номер-issue`.
2. Одно изменение - один PR, так его реально прочитать.
3. Поведение изменилось - обновите или добавьте тесты.
4. Поставьте зафиксированные dev-зависимости и прогоните проверки:
   ```bash
   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --require-hashes -r src/app/requirements-dev.txt

   cd src/app
   TOKEN=dummy PYTHONWARNINGS=error python -m unittest discover -s tests -t . -v
   coverage erase
   TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite unit
   TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite integration
   TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite migration
   coverage combine
   coverage report --fail-under=85
   ruff check .
   ruff format --check .
   ```
5. В описании PR: что поменяли, зачем, как проверяли и какой Issue закрывает.

## Требования к коду

- PEP 8, а окончательный судья по стилю - Ruff (линтер и форматтер).
- Тексты, команды и числовые параметры держите в `config.py`, а не в коде модулей.
- Новую логику должно быть видно из кода: маленькие функции, понятные имена, комментарий там, где объясняется причина решения.
- Никаких секретов в Git: `.env`, база SQLite и логи не коммитятся.

Карта всех разделов документации - в [docs/index.md](docs/index.md).
