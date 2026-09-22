import json
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


def get_user_tickets(guild_id, user_id):
    """Все тикеты пользователя на сервере (для удаления данных)."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id
            """,
            (guild_id, user_id),
        )
        return c.fetchall()
    finally:
        conn.close()


def add_log_message_id(channel_id, thread_id, message_id):
    """Запоминает сообщение лог-центра, связанное с тикетом.

    Хранится JSON-массив пар [thread_id, message_id] — по ним удаление
    персональных данных и ретенция находят и стирают логовые вложения
    (транскрипты) в Discord.
    """
    conn = get_db()
    try:
        c = conn.cursor()
        row = c.execute(
            "SELECT log_message_ids FROM tickets WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if not row:
            return False
        refs = parse_log_message_refs(row["log_message_ids"])
        refs.append([int(thread_id), int(message_id)])
        c.execute(
            "UPDATE tickets SET log_message_ids = ? WHERE channel_id = ?",
            (json.dumps(refs), channel_id),
        )
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def parse_log_message_refs(raw):
    """JSON из tickets.log_message_ids -> список пар (thread_id, message_id)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    refs = []
    for item in data:
        try:
            thread_id, message_id = item
            refs.append((int(thread_id), int(message_id)))
        except (TypeError, ValueError):
            continue
    return refs


def anonymize_user_tickets(guild_id, user_id):
    """Удаляет персональные поля пользователя из тикетов сервера.

    Ответы формы, имя и ID заявителя стираются, связи с сообщениями
    лог-центра очищаются (сами сообщения удаляет вызывающий код), открытые
    тикеты переводятся в закрытые: их каналы на этом шаге уже удалены.
    Агрегированная статистика остаётся: она не содержит персональных данных.
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
                reason = NULL,
                log_message_ids = NULL,
                status = CASE WHEN status = 'open' THEN 'closed' ELSE status END,
                closed_at = CASE
                    WHEN status = 'open' AND closed_at IS NULL THEN ?
                    ELSE closed_at
                END
            WHERE guild_id = ? AND user_id = ?
            """,
            (
                ANONYMIZED_USER_ID,
                ANONYMIZED_USER_NAME,
                datetime.now().isoformat(),
                guild_id,
                user_id,
            ),
        )
        changed = c.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()


def get_retention_expired(cutoff_iso):
    """Тикеты старше срока хранения.

    Закрытые — по дате закрытия, открытые (заброшенные) — по дате создания.
    """
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM tickets
            WHERE (status != 'open' AND closed_at IS NOT NULL AND closed_at < ?)
               OR (status = 'open' AND created_at < ?)
            ORDER BY id
            """,
            (cutoff_iso, cutoff_iso),
        )
        return c.fetchall()
    finally:
        conn.close()


def delete_ticket_by_id(ticket_id):
    """Полностью удаляет запись тикета (используется ретенцией)."""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        deleted = c.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()
