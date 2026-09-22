"""Служебное key-value хранилище (таблица bot_state).

Используется для ID объектов, которые бот создал сам (лог-канал, ветки):
после перезапуска бот находит их по сохранённому ID и не плодит дубликаты.
"""

from .db import get_db


def get_state(key: str) -> str | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_state(key: str, value: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO bot_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def delete_state(key: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM bot_state WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()
