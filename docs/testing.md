# Тесты, coverage и линтер

## Установка проверяемого окружения

Runtime- и dev-зависимости закреплены с SHA-256-хешами. Для разработки нужен именно dev lock:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r src/app/requirements-dev.txt
```

`requirements.in` и `requirements-dev.in` — входные ограничения для обновления, а `requirements.txt` и `requirements-dev.txt` — воспроизводимые lock-файлы. Не редактируйте версии и хеши в lock-файлах вручную.

## Запуск тестов

Полный быстрый прогон:

```bash
cd src/app
TOKEN=dummy PYTHONWARNINGS=error python -m unittest discover -s tests -t . -v
```

`-t .` важен: тесты импортируются как пакет `tests`, поэтому до первого `import discord` включается политика warnings-as-errors. Единственное временное исключение для стороннего `audioop` имеет владельца и срок пересмотра в `tests/__init__.py`. Любой новый `RuntimeWarning` или `DeprecationWarning`, включая `coroutine was never awaited`, делает прогон красным.

CI делит набор на непересекающиеся группы, которые можно запустить отдельно:

```bash
python -m tests.run_suite unit        # быстрые unit/contract tests
python -m tests.run_suite integration # связки модулей, reconcile и retention
python -m tests.run_suite migration   # миграции и lifecycle соединения БД
```

Переменная `TOKEN` нужна только потому, что тесты проверяют обязательность токена; подходит фиктивное значение. Реальная сеть Discord не используется.

## Coverage

Локальный эквивалент CI:

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

CI требует минимум **85% branch coverage** и для всего приложения, и отдельно для критических доменных/DB-модулей `afk`, `database`, `tickets`. XML-отчёт публикуется как workflow artifact на 14 дней.

## Что покрыто

Тесты используют `unittest`, async-фейки и временную SQLite без реального Discord:

| Файлы | Что проверяют |
|---|---|
| `test_config.py` | Разбор, нормализацию и валидацию настроек |
| `test_database.py`, `test_afk_database.py`, `test_db_connection.py` | CRUD, транзакции и lifecycle SQLite |
| `test_migrations.py` | Preflight, backup и миграции схемы |
| `test_tickets*.py`, `test_commands.py` | Формы, Discord-контракты, гонки и lifecycle тикетов |
| `test_afk_*.py` | Модели, команды, события, таймауты UI и фоновые задачи AFK |
| `test_logcenter.py` | Приватность, доставка, архив и конкурентное создание веток |
| `test_integration.py`, `test_reconcile.py`, `test_retention.py` | Межмодульные и отказоустойчивые сценарии |

`tests/support.py` содержит контрактные async-фейки: корутинные методы представлены `AsyncMock`, acknowledgement interaction отслеживается, `allowed_mentions` и Discord permissions проверяются явно. Обычный `MagicMock` вместо awaitable запрещён политикой предупреждений.

## Ruff

Проверки используют версию из dev lock и конфигурацию `pyproject.toml`:

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

Хуки проверяют Ruff, хвостовые пробелы, конец файла и YAML до коммита.

## Правила для новых тестов

- Один тест — один наблюдаемый инвариант, а не только факт вызова mock-метода.
- Для Discord API используйте async-фейки из `tests/support.py` и проверяйте interaction acknowledgement, permissions и `allowed_mentions`.
- Для БД используйте временный путь через `use_temp_database`; боевой `database.db` тесты не трогают.
- Для каждой исправленной гонки, P0/P1 или data-integrity ошибки нужен regression test.
- Новый тестовый модуль автоматически попадает в unit-группу; если это integration/migration suite, добавьте его в соответствующий набор `tests/run_suite.py`.
