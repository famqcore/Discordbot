"""Данные заявок: тикеты и дневная статистика.

Жизненный цикл заявки:

```
            create_ticket()                begin_transition()      finalize_transition()
   (нет) ──────────────────► open ─────────────────────────► processing ─────────────────► accepted
                              │                                  │                          denied
                              │                                  └── release_transition() ──► open
                              └────────────── reconciliation ───────────────────────────────► closed
```

- `open` — заявка активна, канал существует;
- `processing` — терминальное действие началось: Discord-операции ещё идут,
  но тикет уже захвачен ровно одним обработчиком (защита от double-click
  и гонки accept/deny/close);
- `accepted` / `denied` / `closed` — конечные состояния, изменению не подлежат.

Все переходы — условные UPDATE внутри одной транзакции: побеждает ровно
одна операция, повторные возвращают признак «уже обработано». Записи с
зависшим `processing` подбирает reconciliation (`tickets/reconcile.py`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any, TypeVar

from utils import clock

from . import db
from .schema import (
    ACTIVE_STATUSES,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_PROCESSING,
    TERMINAL_STATUSES,
)

ANONYMIZED_USER_ID = 0
ANONYMIZED_USER_NAME = "deleted-user"

_ACTIVE_SQL = ", ".join("?" for _ in ACTIVE_STATUSES)
T = TypeVar("T")


async def _run_async(operation: Callable[[], T], *, write: bool = False) -> T:
    """Выполняет синхронную операцию репозитория в потоке шлюза БД."""
    return await db.arun(lambda _connection: operation(), write=write)


def init_db() -> None:
    from .migrations import migrate_schema

    migrate_schema()


# ---------------------------------------------------------------------------
# Создание и чтение
# ---------------------------------------------------------------------------


def save_ticket(
    channel_id: int,
    user_id: int,
    user_name: str | None,
    topic: str,
    ticket_type: str | None,
    answers: str | None,
    created_at: str | None = None,
    guild_id: int = 0,
) -> int:
    """Создаёт запись заявки и возвращает её id.

    Конфликт уникального индекса (у пользователя уже есть активная заявка,
    либо канал уже зарегистрирован) поднимает ``sqlite3.IntegrityError`` —
    вызывающий код обязан убрать за собой созданный канал.
    """
    stamp = created_at or clock.to_db()

    def operation(conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            """
            INSERT INTO tickets
                (guild_id, channel_id, user_id, user_name, topic, type, answers,
                 created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                user_id,
                user_name,
                topic,
                ticket_type,
                answers,
                stamp,
                STATUS_OPEN,
            ),
        )
        return int(cursor.lastrowid)

    return db.run(operation, write=True)


def get_ticket(channel_id: int) -> sqlite3.Row | None:
    return db.run(
        lambda conn: conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    )


def get_ticket_by_id(ticket_id: int) -> sqlite3.Row | None:
    return db.run(
        lambda conn: conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    )


def get_open_ticket_for_user(guild_id: int, user_id: int) -> sqlite3.Row | None:
    """Активная (open или processing) заявка пользователя на сервере."""
    return db.run(
        lambda conn: conn.execute(
            f"""
            SELECT * FROM tickets
            WHERE guild_id = ? AND user_id = ? AND status IN ({_ACTIVE_SQL})
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (guild_id, user_id, *ACTIVE_STATUSES),
        ).fetchone()
    )


def get_active_tickets(guild_id: int | None = None) -> list[sqlite3.Row]:
    """Активные заявки — вход для reconciliation-задачи."""

    def operation(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        if guild_id is None:
            return conn.execute(
                f"SELECT * FROM tickets WHERE status IN ({_ACTIVE_SQL}) ORDER BY id",
                ACTIVE_STATUSES,
            ).fetchall()
        return conn.execute(
            f"SELECT * FROM tickets WHERE guild_id = ? AND status IN ({_ACTIVE_SQL}) ORDER BY id",
            (guild_id, *ACTIVE_STATUSES),
        ).fetchall()

    return db.run(operation)


def get_stale_processing(older_than_iso: str) -> list[sqlite3.Row]:
    """Заявки, застрявшие в processing дольше допустимого."""
    return db.run(
        lambda conn: conn.execute(
            """
            SELECT * FROM tickets
            WHERE status = ? AND (processing_at IS NULL OR processing_at < ?)
            ORDER BY id
            """,
            (STATUS_PROCESSING, older_than_iso),
        ).fetchall()
    )


def get_all_tickets(limit: int = 50, guild_id: int = 0) -> list[sqlite3.Row]:
    return db.run(
        lambda conn: conn.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
    )


def get_user_tickets(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    """Все тикеты пользователя на сервере (для удаления данных)."""
    return db.run(
        lambda conn: conn.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? ORDER BY id",
            (guild_id, user_id),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# Переходы состояний
# ---------------------------------------------------------------------------


def begin_transition(channel_id: int, target_status: str, guild_id: int | None = None) -> bool:
    """Захватывает открытый тикет: `open -> processing`.

    True — захват удался и вызывающий владеет терминальным действием.
    False — тикета нет, он принадлежит другому серверу или уже обработан
    (повторное нажатие, конкурирующее accept/deny/close).
    """
    if target_status not in TERMINAL_STATUSES:
        raise ValueError(f"недопустимый целевой статус заявки: {target_status}")

    def operation(conn: sqlite3.Connection) -> bool:
        params: list[Any] = [STATUS_PROCESSING, target_status, clock.to_db(), channel_id]
        guild_clause = ""
        if guild_id is not None:
            guild_clause = " AND guild_id = ?"
            params.append(guild_id)
        cursor = conn.execute(
            f"""
            UPDATE tickets
            SET status = ?, pending_status = ?, processing_at = ?
            WHERE channel_id = ? AND status = '{STATUS_OPEN}'{guild_clause}
            """,
            params,
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def release_transition(channel_id: int) -> bool:
    """Возвращает захваченный тикет в `open` (временная ошибка Discord)."""

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute(
            f"""
            UPDATE tickets
            SET status = '{STATUS_OPEN}', pending_status = NULL, processing_at = NULL
            WHERE channel_id = ? AND status = '{STATUS_PROCESSING}'
            """,
            (channel_id,),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def finalize_transition(
    channel_id: int,
    status: str,
    closed_by: int | None = None,
    reason: str | None = None,
) -> bool:
    """Фиксирует конечное состояние захваченного тикета.

    Дневная статистика пополняется ровно один раз — в той же транзакции,
    что и смена статуса, и только реальными решениями (accepted/denied).
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"недопустимый конечный статус заявки: {status}")

    def operation(conn: sqlite3.Connection) -> bool:
        now = clock.utcnow()
        # финализировать можно только то намерение, которое было захвачено:
        # иначе параллельное accept могло бы «дофинализировать» чужой claim
        row = conn.execute(
            f"""
            SELECT guild_id FROM tickets
            WHERE channel_id = ? AND status = '{STATUS_PROCESSING}'
              AND pending_status = ?
            """,
            (channel_id, status),
        ).fetchone()
        if row is None:
            return False

        cursor = conn.execute(
            f"""
            UPDATE tickets
            SET status = ?, closed_at = ?, closed_by = ?, reason = ?,
                pending_status = NULL, processing_at = NULL
            WHERE channel_id = ? AND status = '{STATUS_PROCESSING}'
              AND pending_status = ?
            """,
            (status, clock.to_db(now), closed_by, reason, channel_id, status),
        )
        if cursor.rowcount == 0:
            return False

        if status in ("accepted", "denied"):
            _bump_daily_stats(conn, row["guild_id"], status, now)
        return True

    return db.run(operation, write=True)


def _bump_daily_stats(conn: sqlite3.Connection, guild_id: int, status: str, now) -> None:
    conn.execute(
        """
        INSERT INTO stats (guild_id, date, total_applications, accepted, denied)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(guild_id, date) DO UPDATE SET
            total_applications = total_applications + 1,
            accepted = accepted + excluded.accepted,
            denied = denied + excluded.denied
        """,
        (
            guild_id,
            clock.local_date(now),
            1 if status == "accepted" else 0,
            1 if status == "denied" else 0,
        ),
    )


def update_ticket_status(
    channel_id: int,
    status: str,
    closed_by: int | None = None,
    reason: str | None = None,
    guild_id: int | None = None,
) -> bool:
    """Атомарный переход активной заявки сразу в конечное состояние.

    Используется там, где нет промежуточных Discord-операций (например
    reconciliation). Возвращает False, если заявка уже обработана.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"недопустимый конечный статус заявки: {status}")

    def operation(conn: sqlite3.Connection) -> bool:
        now = clock.utcnow()
        params: list[Any] = [channel_id, *ACTIVE_STATUSES]
        guild_clause = ""
        if guild_id is not None:
            guild_clause = " AND guild_id = ?"
            params.append(guild_id)
        row = conn.execute(
            f"""
            SELECT guild_id FROM tickets
            WHERE channel_id = ? AND status IN ({_ACTIVE_SQL}){guild_clause}
            """,
            params,
        ).fetchone()
        if row is None:
            return False

        cursor = conn.execute(
            f"""
            UPDATE tickets
            SET status = ?, closed_at = ?, closed_by = ?, reason = ?,
                pending_status = NULL, processing_at = NULL
            WHERE channel_id = ? AND status IN ({_ACTIVE_SQL})
            """,
            (status, clock.to_db(now), closed_by, reason, channel_id, *ACTIVE_STATUSES),
        )
        if cursor.rowcount == 0:
            return False

        if status in ("accepted", "denied"):
            _bump_daily_stats(conn, row["guild_id"], status, now)
        return True

    return db.run(operation, write=True)


def delete_ticket(channel_id: int) -> bool:
    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute("DELETE FROM tickets WHERE channel_id = ?", (channel_id,))
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def delete_ticket_by_id(ticket_id: int) -> bool:
    """Полностью удаляет запись тикета (используется ретенцией)."""

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        return cursor.rowcount > 0

    return db.run(operation, write=True)


# ---------------------------------------------------------------------------
# Статистика, связи с лог-центром, приватность
# ---------------------------------------------------------------------------


def get_stats(guild_id: int = 0) -> dict[str, Any]:
    def operation(conn: sqlite3.Connection) -> dict[str, Any]:
        counters = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(status = 'accepted') AS accepted,
                SUM(status = 'denied') AS denied,
                SUM(status IN ('open', 'processing')) AS open_count
            FROM tickets
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()
        weekly = conn.execute(
            """
            SELECT date, total_applications, accepted, denied
            FROM stats
            WHERE guild_id = ?
            ORDER BY date DESC
            LIMIT 7
            """,
            (guild_id,),
        ).fetchall()
        return {
            "total": counters["total"] or 0,
            "accepted": counters["accepted"] or 0,
            "denied": counters["denied"] or 0,
            "open": counters["open_count"] or 0,
            "weekly": weekly,
        }

    return db.run(operation)


def add_log_message_id(channel_id: int, thread_id: int, message_id: int) -> bool:
    """Запоминает сообщение лог-центра, связанное с тикетом.

    Хранится JSON-массив пар [thread_id, message_id] — по ним удаление
    персональных данных и ретенция находят и стирают логовые вложения.
    """

    def operation(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT log_message_ids FROM tickets WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return False
        refs = [list(ref) for ref in parse_log_message_refs(row["log_message_ids"])]
        refs.append([int(thread_id), int(message_id)])
        cursor = conn.execute(
            "UPDATE tickets SET log_message_ids = ? WHERE channel_id = ?",
            (json.dumps(refs), channel_id),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def parse_log_message_refs(raw: str | None) -> list[tuple[int, int]]:
    """JSON из tickets.log_message_ids -> список пар (thread_id, message_id)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    refs: list[tuple[int, int]] = []
    for item in data:
        try:
            thread_id, message_id = item
            refs.append((int(thread_id), int(message_id)))
        except (TypeError, ValueError):
            continue
    return refs


def anonymize_user_tickets(guild_id: int, user_id: int) -> int:
    """Удаляет персональные поля пользователя из тикетов сервера.

    Ответы формы, имя и ID заявителя стираются, связи с сообщениями
    лог-центра очищаются (сами сообщения удаляет вызывающий код), активные
    тикеты переводятся в закрытые: их каналы на этом шаге уже удалены.
    Агрегированная статистика остаётся: персональных данных в ней нет.
    """

    def operation(conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            f"""
            UPDATE tickets
            SET user_id = ?,
                user_name = ?,
                answers = '{{}}',
                reason = NULL,
                log_message_ids = NULL,
                pending_status = NULL,
                processing_at = NULL,
                status = CASE
                    WHEN status IN ({_ACTIVE_SQL}) THEN '{STATUS_CLOSED}'
                    ELSE status
                END,
                closed_at = CASE
                    WHEN status IN ({_ACTIVE_SQL}) AND closed_at IS NULL THEN ?
                    ELSE closed_at
                END
            WHERE guild_id = ? AND user_id = ?
            """,
            (
                ANONYMIZED_USER_ID,
                ANONYMIZED_USER_NAME,
                *ACTIVE_STATUSES,
                *ACTIVE_STATUSES,
                clock.to_db(),
                guild_id,
                user_id,
            ),
        )
        return cursor.rowcount

    return db.run(operation, write=True)


def get_retention_expired(cutoff_iso: str) -> list[sqlite3.Row]:
    """Тикеты старше срока хранения.

    Конечные — по дате закрытия, открытые заброшенные — по дате создания.
    ``processing`` принадлежит текущему терминальному действию, поэтому его
    возвращает в ``open`` reconciliation после истечения захвата.
    """
    return db.run(
        lambda conn: conn.execute(
            f"""
            SELECT * FROM tickets
            WHERE (status NOT IN ({_ACTIVE_SQL}) AND closed_at IS NOT NULL AND closed_at < ?)
               OR (status = ? AND created_at < ?)
            ORDER BY id
            """,
            (*ACTIVE_STATUSES, cutoff_iso, STATUS_OPEN, cutoff_iso),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# Асинхронный API для обработчиков Discord
# ---------------------------------------------------------------------------


async def async_save_ticket(
    channel_id: int,
    user_id: int,
    user_name: str | None,
    topic: str,
    ticket_type: str | None,
    answers: str | None,
    created_at: str | None = None,
    guild_id: int = 0,
) -> int:
    return await _run_async(
        lambda: save_ticket(
            channel_id,
            user_id,
            user_name,
            topic,
            ticket_type,
            answers,
            created_at,
            guild_id,
        ),
        write=True,
    )


async def async_get_ticket(channel_id: int) -> sqlite3.Row | None:
    return await _run_async(lambda: get_ticket(channel_id))


async def async_get_open_ticket_for_user(guild_id: int, user_id: int) -> sqlite3.Row | None:
    return await _run_async(lambda: get_open_ticket_for_user(guild_id, user_id))


async def async_get_active_tickets(guild_id: int | None = None) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_active_tickets(guild_id))


async def async_get_stale_processing(older_than_iso: str) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_stale_processing(older_than_iso))


async def async_get_all_tickets(limit: int = 50, guild_id: int = 0) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_all_tickets(limit, guild_id))


async def async_get_user_tickets(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_user_tickets(guild_id, user_id))


async def async_begin_transition(
    channel_id: int, target_status: str, guild_id: int | None = None
) -> bool:
    return await _run_async(
        lambda: begin_transition(channel_id, target_status, guild_id), write=True
    )


async def async_release_transition(channel_id: int) -> bool:
    return await _run_async(lambda: release_transition(channel_id), write=True)


async def async_finalize_transition(
    channel_id: int,
    status: str,
    closed_by: int | None = None,
    reason: str | None = None,
) -> bool:
    return await _run_async(
        lambda: finalize_transition(channel_id, status, closed_by, reason), write=True
    )


async def async_update_ticket_status(
    channel_id: int,
    status: str,
    closed_by: int | None = None,
    reason: str | None = None,
    guild_id: int | None = None,
) -> bool:
    return await _run_async(
        lambda: update_ticket_status(channel_id, status, closed_by, reason, guild_id), write=True
    )


async def async_delete_ticket(channel_id: int) -> bool:
    return await _run_async(lambda: delete_ticket(channel_id), write=True)


async def async_delete_ticket_by_id(ticket_id: int) -> bool:
    return await _run_async(lambda: delete_ticket_by_id(ticket_id), write=True)


async def async_get_stats(guild_id: int = 0) -> dict[str, Any]:
    return await _run_async(lambda: get_stats(guild_id))


async def async_add_log_message_id(channel_id: int, thread_id: int, message_id: int) -> bool:
    return await _run_async(
        lambda: add_log_message_id(channel_id, thread_id, message_id), write=True
    )


async def async_anonymize_user_tickets(guild_id: int, user_id: int) -> int:
    return await _run_async(lambda: anonymize_user_tickets(guild_id, user_id), write=True)


async def async_get_retention_expired(cutoff_iso: str) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_retention_expired(cutoff_iso))
