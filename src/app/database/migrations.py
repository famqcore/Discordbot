"""Версионирование схемы SQLite.

База у бота одна на инстанс, поэтому изменение схемы должно быть
воспроизводимым и безопасным для уже запущенных установок. Миграции ниже
идемпотентны: их можно вызывать при каждом старте, состояние фиксируется в
`PRAGMA user_version`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from sqlite3 import Connection

from .db import get_db

LATEST_SCHEMA_VERSION = 3
LEGACY_GUILD_ID = 0


def migrate_schema() -> None:
    """Применяет все миграции схемы БД."""
    conn = get_db()
    try:
        current_version = _user_version(conn)
        for version, migration in MIGRATIONS:
            if current_version < version:
                migration(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
                current_version = version
    finally:
        conn.close()


def _user_version(conn: Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_exists(conn: Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _drop_table(conn: Connection, name: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {name}")


def _migration_1_initial_schema(conn: Connection) -> None:
    """Базовая схема до разделения данных по guild_id.

    Она совпадает с исторической структурой проекта и нужна, чтобы новая
    установка и старая база проходили через один и тот же путь миграции.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER UNIQUE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            topic TEXT NOT NULL,
            type TEXT,
            answers TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL,
            closed_at TEXT,
            closed_by INTEGER,
            reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            total_applications INTEGER DEFAULT 0,
            accepted INTEGER DEFAULT 0,
            denied INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_users (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            afk_reason TEXT DEFAULT 'Отошёл',
            afk_since TEXT NOT NULL,
            estimated_return TEXT,
            original_nick TEXT,
            is_afk INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, guild_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_cooldown (
            mentioner_id INTEGER NOT NULL,
            afk_user_id INTEGER NOT NULL,
            last_reply TEXT NOT NULL,
            PRIMARY KEY (mentioner_id, afk_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_stats (
            user_id INTEGER PRIMARY KEY,
            total_afk_count INTEGER DEFAULT 0,
            total_afk_seconds INTEGER DEFAULT 0,
            longest_afk_seconds INTEGER DEFAULT 0
        )
        """
    )


def _migration_2_guild_scoped_data(conn: Connection) -> None:
    """Добавляет изоляцию данных по Discord guild_id."""
    _migrate_tickets(conn)
    _migrate_stats(conn)
    _migrate_afk_users(conn)
    _migrate_afk_cooldown(conn)
    _migrate_afk_stats(conn)


def _migrate_tickets(conn: Connection) -> None:
    cols = _columns(conn, "tickets")
    if not cols:
        _create_tickets(conn)
    elif "guild_id" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")

    _close_duplicate_open_tickets(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets(guild_id)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tickets_guild_user_status
        ON tickets(guild_id, user_id, status)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_one_open_per_user
        ON tickets(guild_id, user_id)
        WHERE status = 'open' AND user_id != 0
        """
    )


def _create_tickets(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            topic TEXT NOT NULL,
            type TEXT,
            answers TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL,
            closed_at TEXT,
            closed_by INTEGER,
            reason TEXT
        )
        """
    )


def _close_duplicate_open_tickets(conn: Connection) -> None:
    """Закрывает исторические дубли перед созданием partial unique index."""
    duplicate_groups = conn.execute(
        """
        SELECT guild_id, user_id
        FROM tickets
        WHERE status = 'open' AND user_id != 0
        GROUP BY guild_id, user_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if not duplicate_groups:
        return

    now = datetime.now().isoformat()
    for group in duplicate_groups:
        rows = conn.execute(
            """
            SELECT id
            FROM tickets
            WHERE guild_id = ? AND user_id = ? AND status = 'open'
            ORDER BY created_at DESC, id DESC
            """,
            (group["guild_id"], group["user_id"]),
        ).fetchall()
        duplicate_ids = [row["id"] for row in rows[1:]]
        if not duplicate_ids:
            continue
        placeholders = ",".join("?" for _ in duplicate_ids)
        conn.execute(
            f"""
            UPDATE tickets
            SET status = 'closed',
                closed_at = COALESCE(closed_at, ?),
                reason = COALESCE(reason, 'closed by migration: duplicate open ticket')
            WHERE id IN ({placeholders})
            """,
            (now, *duplicate_ids),
        )


def _migrate_stats(conn: Connection) -> None:
    cols = _columns(conn, "stats")
    if not cols:
        _create_stats(conn)
        return
    if "guild_id" in cols:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_guild_date ON stats(guild_id, date)")
        return

    _drop_table(conn, "stats_v1")
    conn.execute("ALTER TABLE stats RENAME TO stats_v1")
    _create_stats(conn)
    conn.execute(
        """
        INSERT INTO stats (guild_id, date, total_applications, accepted, denied)
        SELECT ?, date, total_applications, accepted, denied
        FROM stats_v1
        """,
        (LEGACY_GUILD_ID,),
    )
    _drop_table(conn, "stats_v1")


def _create_stats(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_applications INTEGER DEFAULT 0,
            accepted INTEGER DEFAULT 0,
            denied INTEGER DEFAULT 0,
            UNIQUE(guild_id, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_guild_date ON stats(guild_id, date)")


def _migrate_afk_users(conn: Connection) -> None:
    cols = _columns(conn, "afk_users")
    if not cols:
        _create_afk_users(conn)
        return
    if "original_nick" not in cols:
        conn.execute("ALTER TABLE afk_users ADD COLUMN original_nick TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_users_guild ON afk_users(guild_id)")


def _create_afk_users(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_users (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            afk_reason TEXT DEFAULT 'Отошёл',
            afk_since TEXT NOT NULL,
            estimated_return TEXT,
            original_nick TEXT,
            is_afk INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, guild_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_users_guild ON afk_users(guild_id)")


def _migrate_afk_cooldown(conn: Connection) -> None:
    cols = _columns(conn, "afk_cooldown")
    if not cols:
        _create_afk_cooldown(conn)
        return
    if "guild_id" in cols:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_afk_cooldown_guild
            ON afk_cooldown(guild_id)
            """
        )
        return

    _drop_table(conn, "afk_cooldown_v1")
    conn.execute("ALTER TABLE afk_cooldown RENAME TO afk_cooldown_v1")
    _create_afk_cooldown(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO afk_cooldown (guild_id, mentioner_id, afk_user_id, last_reply)
        SELECT ?, mentioner_id, afk_user_id, last_reply
        FROM afk_cooldown_v1
        """,
        (LEGACY_GUILD_ID,),
    )
    _drop_table(conn, "afk_cooldown_v1")


def _create_afk_cooldown(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_cooldown (
            guild_id INTEGER NOT NULL,
            mentioner_id INTEGER NOT NULL,
            afk_user_id INTEGER NOT NULL,
            last_reply TEXT NOT NULL,
            PRIMARY KEY (guild_id, mentioner_id, afk_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_afk_cooldown_guild
        ON afk_cooldown(guild_id)
        """
    )


def _migrate_afk_stats(conn: Connection) -> None:
    cols = _columns(conn, "afk_stats")
    if not cols:
        _create_afk_stats(conn)
        return
    if "guild_id" in cols:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_stats_guild ON afk_stats(guild_id)")
        return

    _drop_table(conn, "afk_stats_v1")
    conn.execute("ALTER TABLE afk_stats RENAME TO afk_stats_v1")
    _create_afk_stats(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO afk_stats (
            guild_id, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds
        )
        SELECT ?, user_id, total_afk_count, total_afk_seconds, longest_afk_seconds
        FROM afk_stats_v1
        """,
        (LEGACY_GUILD_ID,),
    )
    _drop_table(conn, "afk_stats_v1")


def _create_afk_stats(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_stats (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            total_afk_count INTEGER DEFAULT 0,
            total_afk_seconds INTEGER DEFAULT 0,
            longest_afk_seconds INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_stats_guild ON afk_stats(guild_id)")


def _migration_3_erasure_support(conn: Connection) -> None:
    """Поддержка удаления данных и управляемого лог-центра.

    tickets.log_message_ids — JSON-массив пар [thread_id, message_id]:
    связь тикета с сообщениями лог-центра (в т.ч. транскриптами), чтобы
    удаление персональных данных и ретенция могли стирать вложения.
    bot_state — служебное key-value хранилище (ID объектов, созданных ботом).
    """
    cols = _columns(conn, "tickets")
    if "log_message_ids" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN log_message_ids TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


MIGRATIONS: tuple[tuple[int, Callable[[Connection], None]], ...] = (
    (1, _migration_1_initial_schema),
    (2, _migration_2_guild_scoped_data),
    (3, _migration_3_erasure_support),
)
