# Тесты, coverage и линтер

## Окружение

Runtime- и dev-зависимости зафиксированы с SHA-256. Для разработки ставим dev-набор:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r src/app/requirements-dev.txt
```

Файлы `requirements.in` и `requirements-dev.in` это то, что мы пишем руками, а `requirements.txt` и `requirements-dev.txt` генерирует `pip-compile`. Версии и хеши в lock-файлах руками не правим.

## Запуск тестов

Весь набор целиком (сейчас это больше 650 тестов):

```bash
cd src/app
TOKEN=dummy PYTHONWARNINGS=error python -m unittest discover -s tests -t . -v
```

Флаг `-t .` важен: тесты импортируются как пакет `tests`, поэтому политика «предупреждение = ошибка» включается до первого `import discord`. Единственное исключение сделано для стороннего `audioop` из discord.py, у него в `tests/__init__.py` прописан владелец и срок пересмотра. Любой новый `DeprecationWarning` или `RuntimeWarning` (включая «coroutine was never awaited») красит прогон в красный.

CI делит тесты на три непересекающиеся группы, их можно запускать отдельно:

```bash
python -m tests.run_suite unit        # быстрые тесты
python -m tests.run_suite integration # связки модулей, reconcile и retention
python -m tests.run_suite migration   # миграции и жизненный цикл соединения с БД
```

`TOKEN` нужен только потому, что часть тестов проверяет обязательность токена, подойдёт любая строка. В сеть Discord тесты не ходят.

## Coverage

Локальный аналог того, что делает CI:

```bash
cd src/app
coverage erase
TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite unit
TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite integration
TOKEN=dummy PYTHONWARNINGS=error coverage run --parallel-mode -m tests.run_suite migration
coverage combine
coverage report --fail-under=85
coverage report --include='afk/*,database/*,tickets/*' --fail-under=85
coverage xml
```

Порог: минимум 85% branch coverage и по всему приложению, и отдельно по модулям `afk`, `database`, `tickets`. XML-отчёт публикуется артефактом CI на 14 дней.

## Что покрыто

Тесты на `unittest`, с async-фейками и временной базой SQLite, без настоящего Discord.

| Файлы | Что проверяют |
|---|---|
| `test_config.py` | Разбор, нормализацию и валидацию настроек |
| `test_database.py`, `test_afk_database.py`, `test_db_connection.py` | CRUD, транзакции, жизненный цикл соединения |
| `test_migrations.py` | Preflight, бэкап и миграции схемы |
| `test_tickets*.py`, `test_commands.py` | Формы, контракты Discord, гонки и закрытие тикетов |
| `test_afk_*.py` | Модели, команды, события, разбор времени, UI и фоновые задачи AFK |
| `test_logcenter.py` | Приватность, доставку, архивные ветки и конкурентное создание |
| `test_integration.py`, `test_reconcile.py`, `test_retention.py` | Межмодульные сценарии и устойчивость к сбоям |
| `test_errors.py`, `test_ratelimit.py`, `test_clock.py`, `test_mentions.py` | Общие утилиты |

В `tests/support.py` лежат контрактные async-фейки: корутины это `AsyncMock`, факт ответа на interaction отслеживается, `allowed_mentions` и права Discord проверяются явно. Подсовывать обычный `MagicMock` вместо awaitable нельзя, политика предупреждений это поймает.

## Ruff

Линтер и форматтер берутся из dev-lock, настройки в `pyproject.toml`:

```bash
ruff check src/app
ruff format --check src/app
```

Автоисправление:

```bash
ruff check --fix src/app
ruff format src/app
```

## Pre-commit

```bash
python -m pip install pre-commit
pre-commit install
```

Хуки прогоняют Ruff, чистят хвостовые пробелы, чинят конец файла, проверяют YAML и следы конфликтов.

## Правила для новых тестов

- Один тест проверяет один наблюдаемый факт, а не то, что мок был вызван.
- Для Discord API берите фейки из `tests/support.py` и проверяйте ответ на interaction, права и `allowed_mentions`.
- Для базы используйте временный путь через `use_temp_database`, боевой `database.db` тесты не трогают.
- На каждую исправленную гонку или потерю данных нужен regression-тест.
- Новый модуль автоматически попадает в группу unit. Если это integration или migration, впишите его в нужный набор в `tests/run_suite.py`.
