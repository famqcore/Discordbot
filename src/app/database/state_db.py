"""Служебное key-value хранилище (таблица bot_state).

Используется для ID объектов, которые бот создал сам (лог-канал, ветки):
после перезапуска бот находит их по сохранённому ID и не плодит дубликаты.
"""

from __future__ import annotations

import sqlite3

from . import db


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
