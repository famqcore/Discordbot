"""Каноническое описание схемы SQLite.

Один источник правды для миграций и для проверки фактической структуры
базы (issue #18): миграция обязана привести базу именно к этим таблицам,
первичным ключам, уникальным ограничениям и индексам, а preflight —
заметить расхождение до того, как приложение начнёт делать upsert
по несуществующему уникальному индексу.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Допустимые статусы заявки и переходы между ними (issues #2, #3).
STATUS_OPEN = "open"
STATUS_PROCESSING = "processing"
STATUS_ACCEPTED = "accepted"
STATUS_DENIED = "denied"
STATUS_CLOSED = "closed"

TERMINAL_STATUSES = (STATUS_ACCEPTED, STATUS_DENIED, STATUS_CLOSED)
ACTIVE_STATUSES = (STATUS_OPEN, STATUS_PROCESSING)
ALL_STATUSES = (STATUS_OPEN, STATUS_PROCESSING, *TERMINAL_STATUSES)

STATUS_LIST_SQL = ", ".join(f"'{status}'" for status in ALL_STATUSES)
ACTIVE_LIST_SQL = ", ".join(f"'{status}'" for status in ACTIVE_STATUSES)


@dataclass(frozen=True)
class Table:
    name: str
    create_sql: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    # уникальные наборы колонок (без учёта partial-условий)
    unique: tuple[tuple[str, ...], ...] = ()
    not_null: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()
    # колонки со временем: нормализуются в UTC при миграции v4
    datetime_columns: tuple[str, ...] = ()
    copy_defaults: dict[str, str] = field(default_factory=dict)


TICKETS = Table(
    name="tickets",
    create_sql=f"""
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL DEFAULT 0,
            channel_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            topic TEXT NOT NULL,
            type TEXT,
            answers TEXT,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ({STATUS_LIST_SQL})),
            created_at TEXT NOT NULL,
            closed_at TEXT,
            closed_by INTEGER,
            reason TEXT,
            log_message_ids TEXT,
            pending_status TEXT,
            processing_at TEXT
        )
    """,
    columns=(
        "id",
        "guild_id",
        "channel_id",
        "user_id",
        "user_name",
        "topic",
        "type",
        "answers",
        "status",
        "created_at",
        "closed_at",
        "closed_by",
        "reason",
        "log_message_ids",
        "pending_status",
        "processing_at",
    ),
    primary_key=("id",),
    unique=(("channel_id",),),
    not_null=("guild_id", "channel_id", "user_id", "topic", "status", "created_at"),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets(guild_id)",
        """
        CREATE INDEX IF NOT EXISTS idx_tickets_guild_user_status
        ON tickets(guild_id, user_id, status)
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_one_open_per_user
        ON tickets(guild_id, user_id)
        WHERE status IN ({ACTIVE_LIST_SQL}) AND user_id != 0
        """,
        "CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON tickets(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_closed_at ON tickets(closed_at)",
    ),
    datetime_columns=("created_at", "closed_at", "processing_at"),
    copy_defaults={"guild_id": "0", "status": "'open'"},
)

STATS = Table(
    name="stats",
    create_sql="""
        CREATE TABLE stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL DEFAULT 0,
            date TEXT NOT NULL,
            total_applications INTEGER NOT NULL DEFAULT 0,
            accepted INTEGER NOT NULL DEFAULT 0,
            denied INTEGER NOT NULL DEFAULT 0,
            UNIQUE(guild_id, date)
        )
    """,
    columns=("id", "guild_id", "date", "total_applications", "accepted", "denied"),
    primary_key=("id",),
    unique=(("guild_id", "date"),),
    not_null=("guild_id", "date"),
    indexes=("CREATE INDEX IF NOT EXISTS idx_stats_guild_date ON stats(guild_id, date)",),
    copy_defaults={"guild_id": "0"},
)

AFK_USERS = Table(
    name="afk_users",
    create_sql="""
        CREATE TABLE afk_users (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL DEFAULT 0,
            afk_reason TEXT DEFAULT 'Отошёл',
            afk_since TEXT NOT NULL,
            estimated_return TEXT,
            original_nick TEXT,
            nick_applied INTEGER NOT NULL DEFAULT 0,
            is_afk INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, guild_id)
        )
    """,
    columns=(
        "user_id",
        "guild_id",
        "afk_reason",
        "afk_since",
        "estimated_return",
        "original_nick",
        "nick_applied",
        "is_afk",
    ),
    primary_key=("user_id", "guild_id"),
    unique=(("user_id", "guild_id"),),
    not_null=("user_id", "guild_id", "afk_since", "is_afk"),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_afk_users_guild ON afk_users(guild_id)",
        """
        CREATE INDEX IF NOT EXISTS idx_afk_users_expiry
        ON afk_users(guild_id, is_afk, estimated_return)
        """,
    ),
    datetime_columns=("afk_since", "estimated_return"),
    copy_defaults={"guild_id": "0", "is_afk": "1", "nick_applied": "0"},
)

AFK_COOLDOWN = Table(
    name="afk_cooldown",
    create_sql="""
        CREATE TABLE afk_cooldown (
            guild_id INTEGER NOT NULL DEFAULT 0,
            mentioner_id INTEGER NOT NULL,
            afk_user_id INTEGER NOT NULL,
            last_reply TEXT NOT NULL,
            PRIMARY KEY (guild_id, mentioner_id, afk_user_id)
        )
    """,
    columns=("guild_id", "mentioner_id", "afk_user_id", "last_reply"),
    primary_key=("guild_id", "mentioner_id", "afk_user_id"),
    unique=(("guild_id", "mentioner_id", "afk_user_id"),),
    not_null=("guild_id", "mentioner_id", "afk_user_id", "last_reply"),
    indexes=(
        "CREATE INDEX IF NOT EXISTS idx_afk_cooldown_guild ON afk_cooldown(guild_id)",
        "CREATE INDEX IF NOT EXISTS idx_afk_cooldown_last_reply ON afk_cooldown(last_reply)",
    ),
    datetime_columns=("last_reply",),
    copy_defaults={"guild_id": "0"},
)

AFK_STATS = Table(
    name="afk_stats",
    create_sql="""
        CREATE TABLE afk_stats (
            guild_id INTEGER NOT NULL DEFAULT 0,
            user_id INTEGER NOT NULL,
            total_afk_count INTEGER NOT NULL DEFAULT 0,
            total_afk_seconds INTEGER NOT NULL DEFAULT 0,
            longest_afk_seconds INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """,
    columns=(
        "guild_id",
        "user_id",
        "total_afk_count",
        "total_afk_seconds",
        "longest_afk_seconds",
    ),
    primary_key=("guild_id", "user_id"),
    unique=(("guild_id", "user_id"),),
    not_null=("guild_id", "user_id"),
    indexes=("CREATE INDEX IF NOT EXISTS idx_afk_stats_guild ON afk_stats(guild_id)",),
    copy_defaults={"guild_id": "0"},
)

BOT_STATE = Table(
    name="bot_state",
    create_sql="""
        CREATE TABLE bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    columns=("key", "value"),
    primary_key=("key",),
    unique=(("key",),),
    not_null=("value",),
)

TABLES: tuple[Table, ...] = (TICKETS, STATS, AFK_USERS, AFK_COOLDOWN, AFK_STATS, BOT_STATE)
TABLES_BY_NAME = {table.name: table for table in TABLES}
