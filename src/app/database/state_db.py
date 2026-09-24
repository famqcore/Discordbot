"""Служебное key-value хранилище (таблица bot_state).

Используется для ID объектов, которые бот создал сам (лог-канал, ветки):
после перезапуска бот находит их по сохранённому ID и не плодит дубликаты.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from . import db

T = TypeVar("T")


def get_state(key: str) -> str | None:
    row = db.run(
        lambda conn: conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    )
    return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO bot_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    db.run(operation, write=True)


def delete_state(key: str) -> None:
    db.run(
        lambda conn: conn.execute("DELETE FROM bot_state WHERE key = ?", (key,)),
        write=True,
    )


async def _run_async(operation: Callable[[], T], *, write: bool = False) -> T:
    """Выполняет синхронную операцию репозитория в потоке шлюза БД."""
    return await db.arun(lambda _connection: operation(), write=write)


async def async_get_state(key: str) -> str | None:
    return await _run_async(lambda: get_state(key))


async def async_set_state(key: str, value: str) -> None:
    await _run_async(lambda: set_state(key, value), write=True)


async def async_delete_state(key: str) -> None:
    await _run_async(lambda: delete_state(key), write=True)
