"""Запуск непересекающихся групп тестов для локального feedback и CI."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

INTEGRATION_MODULES = {
    "test_integration",
    "test_reconcile",
    "test_retention",
}
MIGRATION_MODULES = {
    "test_db_connection",
    "test_migrations",
}


def modules_for(suite_name: str) -> list[str]:
    """Возвращает отсортированный список модулей выбранной группы."""
    all_modules = {path.stem for path in Path(__file__).parent.glob("test_*.py")}
    if suite_name == "integration":
        selected = INTEGRATION_MODULES
    elif suite_name == "migration":
        selected = MIGRATION_MODULES
    elif suite_name == "unit":
        selected = all_modules - INTEGRATION_MODULES - MIGRATION_MODULES
    else:
        choices = ", ".join(("unit", "integration", "migration"))
        raise ValueError(f"неизвестная группа {suite_name!r}; выберите: {choices}")

    missing = selected - all_modules
    if missing:
        raise RuntimeError(f"в группе указаны отсутствующие тесты: {sorted(missing)}")
    return [f"tests.{name}" for name in sorted(selected)]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m tests.run_suite {unit|integration|migration}", file=sys.stderr)
        return 2

    try:
        modules = modules_for(args[0])
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 2

    tests = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
