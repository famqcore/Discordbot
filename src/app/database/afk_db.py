from datetime import datetime

from .db import get_db
from .migrations import migrate_schema


def init_afk_db():
    migrate_schema()


def set_afk(
    user_id: int,
    guild_id: int,
    reason: str,
    afk_since: str,
    estimated_return: str | None = None,
    original_nick: str | None = None,
):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO afk_users
                (user_id, guild_id, afk_reason, afk_since, estimated_return, original_nick, is_afk)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                afk_reason = excluded.afk_reason,
                afk_since = excluded.afk_since,
                estimated_return = excluded.estimated_return,
                original_nick = excluded.original_nick,
                is_afk = 1
        """,
            (user_id, guild_id, reason, afk_since, estimated_return, original_nick),
        )
        conn.commit()
    finally:
        conn.close()


def remove_afk(user_id: int, guild_id: int) -> bool:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM afk_users WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        deleted = c.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def get_afk_user(user_id: int, guild_id: int):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM afk_users WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        return c.fetchone()
    finally:
        conn.close()


def get_all_afk(guild_id: int) -> list:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM afk_users WHERE guild_id = ? AND is_afk = 1 ORDER BY afk_since ASC
        """,
            (guild_id,),
        )
        return c.fetchall()
    finally:
        conn.close()


def get_expired_afk(guild_id: int, now_iso: str) -> list:
    """AFK-записи сервера, у которых время возврата уже наступило.

    Сравнение строк корректно: даты хранятся в ISO-формате datetime.isoformat().
    """
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM afk_users
            WHERE guild_id = ? AND is_afk = 1
              AND estimated_return IS NOT NULL AND estimated_return <= ?
            ORDER BY afk_since ASC
        """,
            (guild_id, now_iso),
        )
        return c.fetchall()
    finally:
        conn.close()


def check_cooldown(
    mentioner_id: int,
    afk_user_id: int,
    cooldown_seconds: int = 30,
    guild_id: int = 0,
) -> bool:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT last_reply
            FROM afk_cooldown
            WHERE guild_id = ? AND mentioner_id = ? AND afk_user_id = ?
        """,
            (guild_id, mentioner_id, afk_user_id),
        )
        row = c.fetchone()
    finally:
        conn.close()
    if not row:
        return True
    last = datetime.fromisoformat(row["last_reply"])
    return (datetime.now() - last).total_seconds() >= cooldown_seconds


def set_cooldown(mentioner_id: int, afk_user_id: int, guild_id: int = 0):
    conn = get_db()
    try:
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute(
            """
            INSERT INTO afk_cooldown (guild_id, mentioner_id, afk_user_id, last_reply)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, mentioner_id, afk_user_id) DO UPDATE SET
                last_reply = excluded.last_reply
        """,
            (guild_id, mentioner_id, afk_user_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_cooldowns(before_iso: str) -> int:
    """Удаляет записи кулдауна старше даты (таблица не должна расти бесконечно)."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM afk_cooldown WHERE last_reply < ?", (before_iso,))
        removed = c.rowcount
        conn.commit()
        return removed
    finally:
        conn.close()


def get_user_stats(user_id: int, guild_id: int | None = None):
    conn = get_db()
    try:
        c = conn.cursor()
        if guild_id is None:
            c.execute(
                "SELECT * FROM afk_stats WHERE user_id = ? ORDER BY guild_id LIMIT 1",
                (user_id,),
            )
        else:
            c.execute(
                "SELECT * FROM afk_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        return c.fetchone()
    finally:
        conn.close()


def update_stats_on_set(user_id: int, guild_id: int = 0):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO afk_stats (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
            VALUES (?, ?, 1, 0, 0)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_afk_count = total_afk_count + 1
        """,
            (guild_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_stats_on_remove(user_id: int, afk_seconds: int, guild_id: int = 0):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO afk_stats (guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_afk_seconds = total_afk_seconds + excluded.total_afk_seconds,
                longest_afk_seconds = CASE
                    WHEN longest_afk_seconds < excluded.longest_afk_seconds
                    THEN excluded.longest_afk_seconds
                    ELSE longest_afk_seconds
                END
        """,
            (guild_id, user_id, afk_seconds, afk_seconds),
        )
        conn.commit()
    finally:
        conn.close()


def delete_user_data(user_id: int, guild_id: int) -> dict[str, int]:
    """Удаляет AFK-данные пользователя в рамках одного сервера."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM afk_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        users = c.rowcount
        c.execute("DELETE FROM afk_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        stats = c.rowcount
        c.execute(
            """
            DELETE FROM afk_cooldown
            WHERE guild_id = ? AND (mentioner_id = ? OR afk_user_id = ?)
            """,
            (guild_id, user_id, user_id),
        )
        cooldowns = c.rowcount
        conn.commit()
        return {"afk_users": users, "afk_stats": stats, "afk_cooldown": cooldowns}
    finally:
        conn.close()
