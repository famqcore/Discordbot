from datetime import datetime

from .db import get_db
from .migrations import migrate_schema

ANONYMIZED_USER_ID = 0
ANONYMIZED_USER_NAME = "deleted-user"


def init_db():
    migrate_schema()


def save_ticket(
    channel_id,
    user_id,
    user_name,
    topic,
    ticket_type,
    answers,
    created_at,
    guild_id=0,
):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO tickets
            (guild_id, channel_id, user_id, user_name, topic, type, answers, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
            (guild_id, channel_id, user_id, user_name, topic, ticket_type, answers, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_ticket(channel_id):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        return c.fetchone()
    finally:
        conn.close()


def get_open_ticket_for_user(guild_id, user_id):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND user_id = ? AND status = 'open'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (guild_id, user_id),
        )
        return c.fetchone()
    finally:
        conn.close()


def delete_ticket(channel_id):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM tickets WHERE channel_id = ?", (channel_id,))
        conn.commit()
    finally:
        conn.close()


def update_ticket_status(channel_id, status, closed_by=None, reason=None):
    """Переводит открытый тикет в новый статус.

    Возвращает True, если тикет был открыт и обновлён; False — если тикета
    нет или он уже обработан (повторное нажатие статистику не портит).
    Статистика пополняется только реальными решениями (accepted/denied).
    """
    conn = get_db()
    try:
        c = conn.cursor()
        now = datetime.now()
        ticket = c.execute(
            "SELECT guild_id FROM tickets WHERE channel_id = ? AND status = 'open'",
            (channel_id,),
        ).fetchone()
        if not ticket:
            return False
        guild_id = ticket["guild_id"]

        c.execute(
            """
            UPDATE tickets
            SET status = ?, closed_at = ?, closed_by = ?, reason = ?
            WHERE channel_id = ? AND status = 'open'
        """,
            (status, now.isoformat(), closed_by, reason, channel_id),
        )
        if c.rowcount == 0:
            return False

        if status in ("accepted", "denied"):
            date = now.strftime("%Y-%m-%d")
            accepted = 1 if status == "accepted" else 0
            denied = 1 if status == "denied" else 0

            c.execute(
                """
                INSERT INTO stats (guild_id, date, total_applications, accepted, denied)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(guild_id, date) DO UPDATE SET
                    total_applications = total_applications + 1,
                    accepted = accepted + excluded.accepted,
                    denied = denied + excluded.denied
                """,
                (guild_id, date, accepted, denied),
            )

        conn.commit()
        return True
    finally:
        conn.close()


def get_stats(guild_id=0):
    conn = get_db()
    try:
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM tickets WHERE guild_id = ?", (guild_id,))
        total = c.fetchone()[0] or 0

        c.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'accepted'",
            (guild_id,),
        )
        accepted = c.fetchone()[0] or 0

        c.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'denied'",
            (guild_id,),
        )
        denied = c.fetchone()[0] or 0

        c.execute(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'open'",
            (guild_id,),
        )
        open_count = c.fetchone()[0] or 0

        c.execute(
            """
            SELECT date, total_applications, accepted, denied
            FROM stats
            WHERE guild_id = ?
            ORDER BY date DESC
            LIMIT 7
            """,
            (guild_id,),
        )
        weekly = c.fetchall()

        return {
            "total": total,
            "accepted": accepted,
            "denied": denied,
            "open": open_count,
            "weekly": weekly,
        }
    finally:
        conn.close()


def get_all_tickets(limit=50, guild_id=0):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (guild_id, limit),
        )
        return c.fetchall()
    finally:
        conn.close()


def anonymize_user_tickets(guild_id, user_id):
    """Удаляет персональные поля пользователя из тикетов сервера.

    Агрегированная статистика остаётся: она уже не содержит персональных данных.
    """
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            UPDATE tickets
            SET user_id = ?,
                user_name = ?,
                answers = '{}',
                reason = NULL
            WHERE guild_id = ? AND user_id = ?
            """,
            (ANONYMIZED_USER_ID, ANONYMIZED_USER_NAME, guild_id, user_id),
        )
        changed = c.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()
