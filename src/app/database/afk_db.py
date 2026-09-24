"""Данные AFK: активные статусы, кулдаун автоответов, статистика.

Ключевые инварианты:

- повторная установка AFK не перезаписывает `afk_since` и `original_nick`
  активной сессии: исходный ник обязан пережить любое число обновлений
  причины и времени;
- снятие AFK — одна транзакция «прочитать, удалить, обновить статистику»:
  из двух конкурентных снятий снимок получает только победившая операция,
  поэтому длительность и рекорд не удваиваются;
- кулдаун автоответа резервируется условным upsert до отправки сообщения,
  а при ошибке отправки освобождается — два параллельных упоминания дают
  максимум один автоответ.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

from utils import clock

from . import db

T = TypeVar("T")


@dataclass(frozen=True)
class AfkSetResult:
    """Итог установки AFK: новая сессия или обновление существующей."""

    created: bool

    @property
    def updated(self) -> bool:
        return not self.created


def init_afk_db() -> None:
    from .migrations import migrate_schema

    migrate_schema()


# ---------------------------------------------------------------------------
# Активные AFK
# ---------------------------------------------------------------------------


def set_afk(
    user_id: int,
    guild_id: int,
    reason: str,
    afk_since: str | None = None,
    estimated_return: str | None = None,
    original_nick: str | None = None,
    nick_applied: bool = False,
) -> AfkSetResult:
    """Ставит или обновляет AFK одной транзакцией.

    ``created`` означает, что началась новая сессия и увеличена статистика.
    При обновлении сохраняются время начала и исходный ник активной сессии.
    """
    started = afk_since or clock.to_db()

    def operation(conn: sqlite3.Connection) -> AfkSetResult:
        existing = conn.execute(
            "SELECT afk_since FROM afk_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO afk_users
                    (user_id, guild_id, afk_reason, afk_since, estimated_return,
                     original_nick, nick_applied, is_afk)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    user_id,
                    guild_id,
                    reason,
                    started,
                    estimated_return,
                    original_nick,
                    1 if nick_applied else 0,
                ),
            )
            conn.execute(
                """
                INSERT INTO afk_stats
                    (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
                VALUES (?, ?, 1, 0, 0)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    total_afk_count = total_afk_count + 1
                """,
                (guild_id, user_id),
            )
            return AfkSetResult(created=True)

        conn.execute(
            """
            UPDATE afk_users
            SET afk_reason = ?, estimated_return = ?, is_afk = 1
            WHERE user_id = ? AND guild_id = ?
            """,
            (reason, estimated_return, user_id, guild_id),
        )
        return AfkSetResult(created=False)

    return db.run(operation, write=True)


def mark_nick_applied(user_id: int, guild_id: int, applied: bool = True) -> bool:
    """Отмечает, что префикс ника поставил бот (владение префиксом)."""

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute(
            "UPDATE afk_users SET nick_applied = ? WHERE user_id = ? AND guild_id = ?",
            (1 if applied else 0, user_id, guild_id),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def take_afk(user_id: int, guild_id: int) -> dict | None:
    """Compare-and-delete: снимает AFK и обновляет статистику одной транзакцией.

    Возвращает снимок снятой сессии (включая вычисленную длительность) или
    None, если записи уже нет — значит, её сняла конкурирующая операция и
    статистику обновила именно она.
    """

    def operation(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute(
            "SELECT * FROM afk_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ).fetchone()
        if row is None:
            return None

        cursor = conn.execute(
            "DELETE FROM afk_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        if cursor.rowcount == 0:
            return None

        snapshot = dict(row)
        duration = clock.seconds_between(clock.parse_db(snapshot.get("afk_since")))
        snapshot["duration_seconds"] = duration

        conn.execute(
            """
            INSERT INTO afk_stats
                (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_afk_seconds = total_afk_seconds + excluded.total_afk_seconds,
                longest_afk_seconds = MAX(longest_afk_seconds, excluded.longest_afk_seconds)
            """,
            (guild_id, user_id, duration, duration),
        )
        return snapshot

    return db.run(operation, write=True)


def remove_afk(user_id: int, guild_id: int) -> bool:
    """Удаляет запись AFK без обновления статистики (служебная операция)."""

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute(
            "DELETE FROM afk_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def get_afk_user(user_id: int, guild_id: int) -> sqlite3.Row | None:
    return db.run(
        lambda conn: conn.execute(
            "SELECT * FROM afk_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ).fetchone()
    )


def get_afk_users(guild_id: int, user_ids: Iterable[int]) -> list[sqlite3.Row]:
    """AFK-записи для набора участников одним запросом (автоответ)."""
    ids = [int(user_id) for user_id in dict.fromkeys(user_ids)]
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return db.run(
        lambda conn: conn.execute(
            f"""
            SELECT * FROM afk_users
            WHERE guild_id = ? AND is_afk = 1 AND user_id IN ({placeholders})
            """,
            (guild_id, *ids),
        ).fetchall()
    )


def get_all_afk(guild_id: int) -> list[sqlite3.Row]:
    return db.run(
        lambda conn: conn.execute(
            "SELECT * FROM afk_users WHERE guild_id = ? AND is_afk = 1 ORDER BY afk_since ASC",
            (guild_id,),
        ).fetchall()
    )


def get_expired_afk(guild_id: int, now_iso: str | None = None) -> list[sqlite3.Row]:
    """AFK-записи сервера, у которых время возврата уже наступило.

    Сравнение строк корректно: время хранится в UTC-формате фиксированной
    ширины (``utils.clock.to_db``), поэтому лексикографический порядок
    совпадает с хронологическим.
    """
    cutoff = now_iso or clock.to_db()
    return db.run(
        lambda conn: conn.execute(
            """
            SELECT * FROM afk_users
            WHERE guild_id = ? AND is_afk = 1
              AND estimated_return IS NOT NULL AND estimated_return <= ?
            ORDER BY afk_since ASC
            """,
            (guild_id, cutoff),
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# Кулдаун автоответов
# ---------------------------------------------------------------------------


def reserve_cooldown(
    mentioner_id: int,
    afk_user_id: int,
    cooldown_seconds: int = 30,
    guild_id: int = 0,
) -> bool:
    """Атомарно резервирует право на автоответ.

    True — резерв получен (отправлять можно), False — окно ещё не истекло.
    Проверка и запись выполняются одним условным upsert, поэтому два
    параллельных сообщения не получают по автоответу.
    """
    now = clock.utcnow()
    cutoff = clock.to_db(now - timedelta(seconds=max(cooldown_seconds, 0)))

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute(
            """
            INSERT INTO afk_cooldown (guild_id, mentioner_id, afk_user_id, last_reply)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, mentioner_id, afk_user_id) DO UPDATE SET
                last_reply = excluded.last_reply
            WHERE afk_cooldown.last_reply <= ?
            """,
            (guild_id, mentioner_id, afk_user_id, clock.to_db(now), cutoff),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def release_cooldown(mentioner_id: int, afk_user_id: int, guild_id: int = 0) -> bool:
    """Освобождает резерв, если отправка автоответа не удалась."""

    def operation(conn: sqlite3.Connection) -> bool:
        cursor = conn.execute(
            """
            DELETE FROM afk_cooldown
            WHERE guild_id = ? AND mentioner_id = ? AND afk_user_id = ?
            """,
            (guild_id, mentioner_id, afk_user_id),
        )
        return cursor.rowcount > 0

    return db.run(operation, write=True)


def check_cooldown(
    mentioner_id: int,
    afk_user_id: int,
    cooldown_seconds: int = 30,
    guild_id: int = 0,
) -> bool:
    """Истёк ли кулдаун. Только для чтения: резерв делает ``reserve_cooldown``."""
    row = db.run(
        lambda conn: conn.execute(
            """
            SELECT last_reply FROM afk_cooldown
            WHERE guild_id = ? AND mentioner_id = ? AND afk_user_id = ?
            """,
            (guild_id, mentioner_id, afk_user_id),
        ).fetchone()
    )
    if row is None:
        return True
    last = clock.parse_db(row["last_reply"])
    if last is None:
        return True
    return clock.seconds_between(last) >= cooldown_seconds


def set_cooldown(mentioner_id: int, afk_user_id: int, guild_id: int = 0) -> None:
    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO afk_cooldown (guild_id, mentioner_id, afk_user_id, last_reply)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, mentioner_id, afk_user_id) DO UPDATE SET
                last_reply = excluded.last_reply
            """,
            (guild_id, mentioner_id, afk_user_id, clock.to_db()),
        )

    db.run(operation, write=True)


def cleanup_cooldowns(before_iso: str) -> int:
    """Удаляет записи кулдауна старше даты (таблица не должна расти бесконечно)."""

    def operation(conn: sqlite3.Connection) -> int:
        cursor = conn.execute("DELETE FROM afk_cooldown WHERE last_reply < ?", (before_iso,))
        return cursor.rowcount

    return db.run(operation, write=True)


# ---------------------------------------------------------------------------
# Статистика и приватность
# ---------------------------------------------------------------------------


def get_user_stats(user_id: int, guild_id: int | None = None) -> sqlite3.Row | None:
    def operation(conn: sqlite3.Connection) -> sqlite3.Row | None:
        if guild_id is None:
            return conn.execute(
                "SELECT * FROM afk_stats WHERE user_id = ? ORDER BY guild_id LIMIT 1",
                (user_id,),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM afk_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()

    return db.run(operation)


def update_stats_on_set(user_id: int, guild_id: int = 0) -> None:
    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO afk_stats
                (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
            VALUES (?, ?, 1, 0, 0)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_afk_count = total_afk_count + 1
            """,
            (guild_id, user_id),
        )

    db.run(operation, write=True)


def update_stats_on_remove(user_id: int, afk_seconds: int, guild_id: int = 0) -> None:
    def operation(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO afk_stats
                (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_afk_seconds = total_afk_seconds + excluded.total_afk_seconds,
                longest_afk_seconds = MAX(longest_afk_seconds, excluded.longest_afk_seconds)
            """,
            (guild_id, user_id, afk_seconds, afk_seconds),
        )

    db.run(operation, write=True)


def delete_user_data(user_id: int, guild_id: int) -> dict[str, int]:
    """Удаляет AFK-данные пользователя в рамках одного сервера."""

    def operation(conn: sqlite3.Connection) -> dict[str, int]:
        users = conn.execute(
            "DELETE FROM afk_users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).rowcount
        stats = conn.execute(
            "DELETE FROM afk_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).rowcount
        cooldowns = conn.execute(
            """
            DELETE FROM afk_cooldown
            WHERE guild_id = ? AND (mentioner_id = ? OR afk_user_id = ?)
            """,
            (guild_id, user_id, user_id),
        ).rowcount
        return {"afk_users": users, "afk_stats": stats, "afk_cooldown": cooldowns}

    return db.run(operation, write=True)


# ---------------------------------------------------------------------------
# Асинхронный API для обработчиков Discord
# ---------------------------------------------------------------------------


async def _run_async(operation: Callable[[], T], *, write: bool = False) -> T:
    """Выполняет синхронную операцию репозитория в потоке шлюза БД."""
    return await db.arun(lambda _connection: operation(), write=write)


async def async_set_afk(
    user_id: int,
    guild_id: int,
    reason: str,
    afk_since: str | None = None,
    estimated_return: str | None = None,
    original_nick: str | None = None,
    nick_applied: bool = False,
) -> AfkSetResult:
    return await _run_async(
        lambda: set_afk(
            user_id,
            guild_id,
            reason,
            afk_since,
            estimated_return,
            original_nick,
            nick_applied,
        ),
        write=True,
    )


async def async_mark_nick_applied(user_id: int, guild_id: int, applied: bool = True) -> bool:
    return await _run_async(lambda: mark_nick_applied(user_id, guild_id, applied), write=True)


async def async_take_afk(user_id: int, guild_id: int) -> dict | None:
    return await _run_async(lambda: take_afk(user_id, guild_id), write=True)


async def async_get_afk_user(user_id: int, guild_id: int) -> sqlite3.Row | None:
    return await _run_async(lambda: get_afk_user(user_id, guild_id))


async def async_get_afk_users(guild_id: int, user_ids: Iterable[int]) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_afk_users(guild_id, user_ids))


async def async_get_all_afk(guild_id: int) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_all_afk(guild_id))


async def async_get_expired_afk(guild_id: int, now_iso: str | None = None) -> list[sqlite3.Row]:
    return await _run_async(lambda: get_expired_afk(guild_id, now_iso))


async def async_reserve_cooldown(
    mentioner_id: int,
    afk_user_id: int,
    cooldown_seconds: int = 30,
    guild_id: int = 0,
) -> bool:
    return await _run_async(
        lambda: reserve_cooldown(mentioner_id, afk_user_id, cooldown_seconds, guild_id), write=True
    )


async def async_release_cooldown(mentioner_id: int, afk_user_id: int, guild_id: int = 0) -> bool:
    return await _run_async(
        lambda: release_cooldown(mentioner_id, afk_user_id, guild_id), write=True
    )


async def async_cleanup_cooldowns(before_iso: str) -> int:
    return await _run_async(lambda: cleanup_cooldowns(before_iso), write=True)


async def async_get_user_stats(user_id: int, guild_id: int | None = None) -> sqlite3.Row | None:
    return await _run_async(lambda: get_user_stats(user_id, guild_id))


async def async_delete_user_data(user_id: int, guild_id: int) -> dict[str, int]:
    return await _run_async(lambda: delete_user_data(user_id, guild_id), write=True)
