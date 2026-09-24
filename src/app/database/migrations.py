"""Версионирование схемы SQLite: preflight, backup, verified rebuild.

Схема базы описана в ``database/schema.py`` — это единственный источник
правды. Порядок работы ``migrate_schema``:

1. **Preflight.** Фактическая структура читается через PRAGMA
   (``table_info``, ``index_list``, ``index_info``, ``foreign_key_list``),
   а не по наличию одной колонки. Расхождение по PK/UNIQUE/NOT NULL —
   повод перестроить таблицу, а не молча писать в неё upsert.
2. **Backup.** Перед разрушительным шагом создаётся копия файла БД
   штатным `sqlite3.Connection.backup`. Путь копии пишется в лог.
3. **Rebuild.** Таблица пересоздаётся по канону, данные переносятся
   в одной транзакции, число строк сверяется до и после. Несовпадение —
   откат и отказ с инструкцией оператору.
4. **Verify.** После миграции структура проверяется ещё раз; остаточные
   проблемы поднимают ``SchemaError`` вместо тихого продолжения.

Версия схемы фиксируется в ``PRAGMA user_version`` и пишется в лог.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from sqlite3 import Connection

import config
from utils import clock
from utils.logger import logger

from .db import connect
from .schema import (
    ACTIVE_STATUSES,
    ALL_STATUSES,
    STATUS_CLOSED,
    TABLES,
    Table,
)

LATEST_SCHEMA_VERSION = 4
LEGACY_GUILD_ID = 0
BACKUP_SUFFIX = ".pre-migration"
MAX_BACKUPS = 5


class SchemaError(RuntimeError):
    """Схема не соответствует ожиданиям и не может быть исправлена молча."""


# ---------------------------------------------------------------------------
# Чтение фактической структуры (PRAGMA)
# ---------------------------------------------------------------------------


def table_exists(conn: Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def columns_of(conn: Connection, table: str) -> dict[str, sqlite3.Row]:
    if not table_exists(conn, table):
        return {}
    return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def primary_key_of(conn: Connection, table: str) -> tuple[str, ...]:
    rows = [row for row in conn.execute(f"PRAGMA table_info({table})") if row["pk"]]
    rows.sort(key=lambda row: row["pk"])
    return tuple(row["name"] for row in rows)


def unique_sets_of(conn: Connection, table: str) -> set[frozenset[str]]:
    """Уникальные наборы колонок: из индексов и из объявления PK."""
    found: set[frozenset[str]] = set()
    for index in conn.execute(f"PRAGMA index_list({table})"):
        if not index["unique"]:
            continue
        if index["partial"]:
            # partial unique проверяется отдельно: он не гарантирует upsert
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA index_info({index['name']})")}
        if cols:
            found.add(frozenset(cols))
    pk = primary_key_of(conn, table)
    if pk:
        found.add(frozenset(pk))
    return found


def index_names_of(conn: Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})")}


_INDEX_NAME_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)


def declared_index_names(table: Table) -> set[str]:
    """Имена индексов, объявленных в каноне таблицы."""
    names = set()
    for index_sql in table.indexes:
        match = _INDEX_NAME_RE.search(index_sql)
        if match:
            names.add(match.group(1))
    return names


def missing_indexes(conn: Connection, table: Table) -> set[str]:
    """Индексы канона, которых нет в базе.

    Индекс могли удалить вручную или потерять при восстановлении из дампа.
    Без этой проверки база с корректными колонками считается здоровой,
    а запросы тихо уходят в полное сканирование.
    """
    if not table_exists(conn, table.name):
        return declared_index_names(table)
    return declared_index_names(table) - index_names_of(conn, table.name)


def inspect_table(conn: Connection, table: Table, *, check_indexes: bool = True) -> list[str]:
    """Расхождения фактической таблицы с каноном. Пустой список — всё ок.

    ``check_indexes=False`` оставляет только структурные расхождения — те,
    что лечатся исключительно перестроением таблицы.
    """
    if not table_exists(conn, table.name):
        return [f"таблица {table.name} отсутствует"]

    problems: list[str] = []
    actual_columns = columns_of(conn, table.name)

    missing = [name for name in table.columns if name not in actual_columns]
    if missing:
        problems.append(f"{table.name}: нет колонок {', '.join(missing)}")

    actual_pk = primary_key_of(conn, table.name)
    if set(actual_pk) != set(table.primary_key):
        problems.append(
            f"{table.name}: PRIMARY KEY ({', '.join(actual_pk) or '—'}) "
            f"вместо ({', '.join(table.primary_key)})"
        )

    actual_unique = unique_sets_of(conn, table.name)
    for expected in table.unique:
        if frozenset(expected) not in actual_unique:
            problems.append(f"{table.name}: нет UNIQUE({', '.join(expected)})")

    for name in table.not_null:
        column = actual_columns.get(name)
        if column is not None and not column["notnull"]:
            problems.append(f"{table.name}: колонка {name} допускает NULL")

    if check_indexes:
        absent = missing_indexes(conn, table)
        if absent:
            problems.append(f"{table.name}: нет индексов {', '.join(sorted(absent))}")

    return problems


def inspect_schema(conn: Connection) -> list[str]:
    """Полный preflight по всем таблицам канона."""
    problems: list[str] = []
    for table in TABLES:
        problems.extend(inspect_table(conn, table))
    return problems


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def _backup_path(db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{db_path}{BACKUP_SUFFIX}.{stamp}"


def _prune_backups(db_path: str) -> None:
    directory = os.path.dirname(db_path) or "."
    prefix = f"{os.path.basename(db_path)}{BACKUP_SUFFIX}."
    try:
        names = sorted(name for name in os.listdir(directory) if name.startswith(prefix))
    except OSError:
        return
    for name in names[:-MAX_BACKUPS]:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError as error:
            logger.warning(f"migrations: не удалось удалить старый бэкап {name}: {error}")


def create_backup(conn: Connection, db_path: str | None = None) -> str | None:
    """Копия базы перед разрушительной миграцией. None — БД в памяти."""
    target_db = db_path or config.DB_PATH
    if not target_db or target_db == ":memory:" or not os.path.exists(target_db):
        return None

    path = _backup_path(target_db)
    try:
        backup_conn = connect(path)
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()
        _prune_backups(target_db)
    except (sqlite3.Error, OSError) as error:
        logger.error(f"migrations: не удалось создать бэкап БД: {error}")
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        raise SchemaError(
            "не удалось создать резервную копию базы перед миграцией; "
            "миграция отменена, проверьте свободное место и права на каталог БД"
        ) from error
    logger.info(f"migrations: создан бэкап базы перед миграцией: {path}")
    return path


# ---------------------------------------------------------------------------
# Verified rebuild
# ---------------------------------------------------------------------------


def _row_count(conn: Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def rebuild_table(conn: Connection, table: Table, *, reason: str) -> None:
    """Пересоздаёт таблицу по канону с переносом данных и сверкой строк.

    Вызывается внутри транзакции миграции. При расхождении числа строк
    бросает SchemaError — вызывающий код откатывает транзакцию.
    """
    exists = table_exists(conn, table.name)
    if not exists:
        conn.execute(table.create_sql)
        for index_sql in table.indexes:
            conn.execute(index_sql)
        return

    logger.warning(f"migrations: перестраиваю таблицу {table.name} ({reason})")
    before = _row_count(conn, table.name)
    existing_columns = set(columns_of(conn, table.name))
    temp_name = f"{table.name}__migrating"

    conn.execute(f"DROP TABLE IF EXISTS {temp_name}")
    conn.execute(f"ALTER TABLE {table.name} RENAME TO {temp_name}")
    conn.execute(table.create_sql)

    select_parts = []
    for column in table.columns:
        if column in existing_columns:
            default = table.copy_defaults.get(column)
            if default is not None:
                select_parts.append(f"COALESCE({column}, {default}) AS {column}")
            else:
                select_parts.append(column)
        elif column in table.copy_defaults:
            select_parts.append(f"{table.copy_defaults[column]} AS {column}")
        else:
            select_parts.append(f"NULL AS {column}")

    columns_sql = ", ".join(table.columns)
    conn.execute(
        f"INSERT INTO {table.name} ({columns_sql}) "
        f"SELECT {', '.join(select_parts)} FROM {temp_name}"
    )

    after = _row_count(conn, table.name)
    if after != before:
        raise SchemaError(
            f"перенос таблицы {table.name} потерял данные: было {before} строк, "
            f"перенесено {after}. Миграция отменена, база не изменена — "
            f"восстановите её из бэкапа рядом с файлом БД и сообщите разработчикам"
        )

    conn.execute(f"DROP TABLE {temp_name}")
    for index_sql in table.indexes:
        conn.execute(index_sql)


def ensure_table(conn: Connection, table: Table) -> None:
    """Приводит таблицу к канону: индексы либо полный rebuild.

    Отсутствующий индекс создаётся отдельным ``CREATE INDEX`` — ради него
    перестраивать таблицу не нужно. Перестроение остаётся только для
    структурных расхождений: колонок, PK, UNIQUE и NOT NULL.
    """
    problems = inspect_table(conn, table, check_indexes=False)
    if problems:
        rebuild_table(conn, table, reason="; ".join(problems))
        return
    for index_sql in table.indexes:
        conn.execute(index_sql)


# ---------------------------------------------------------------------------
# Миграции
# ---------------------------------------------------------------------------


def _user_version(conn: Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migration_1_initial_schema(conn: Connection) -> None:
    """Базовая схема. Новая установка и историческая база идут одним путём."""
    for table in TABLES:
        if not table_exists(conn, table.name):
            conn.execute(table.create_sql)
            for index_sql in table.indexes:
                conn.execute(index_sql)


def _migration_2_guild_scoped_data(conn: Connection) -> None:
    """Изоляция данных по guild_id и корректные ограничения."""
    _close_duplicate_active_tickets(conn)
    for table in TABLES:
        ensure_table(conn, table)


def _migration_3_erasure_support(conn: Connection) -> None:
    """Связь тикета с логами (log_message_ids) и bot_state."""
    for table in TABLES:
        ensure_table(conn, table)


def _migration_4_utc_timestamps(conn: Connection) -> None:
    """Единый формат времени: UTC ISO 8601 с явным смещением.

    Наивные значения прошлых версий трактуются как время в
    ``config.LEGACY_TIMEZONE`` (по умолчанию UTC) и переводятся в UTC,
    чтобы сравнение строк в SQL совпадало с хронологией.
    """
    for table in TABLES:
        ensure_table(conn, table)
    for table in TABLES:
        for column in table.datetime_columns:
            _normalize_datetime_column(conn, table.name, column)


def _normalize_datetime_column(conn: Connection, table: str, column: str) -> None:
    rows = conn.execute(
        f"SELECT rowid AS rid, {column} AS value FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} != ''"
    ).fetchall()
    for row in rows:
        parsed = clock.parse_db(row["value"])
        if parsed is None:
            continue
        normalized = clock.to_db(parsed)
        if normalized != row["value"]:
            conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                (normalized, row["rid"]),
            )


def _close_duplicate_active_tickets(conn: Connection) -> None:
    """Закрывает исторические дубли перед созданием partial unique index."""
    if not table_exists(conn, "tickets"):
        return
    available = set(columns_of(conn, "tickets"))
    if not {"status", "user_id"} <= available:
        return

    # В старых схемах колонки guild_id ещё нет — тогда группируем только по
    # пользователю. Литерал в GROUP BY нельзя: SQLite примет его за номер колонки.
    has_guild = "guild_id" in available
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    group_columns = "guild_id, user_id" if has_guild else "user_id"
    groups = conn.execute(
        f"""
        SELECT {group_columns}
        FROM tickets
        WHERE status IN ({placeholders}) AND user_id != 0
        GROUP BY {group_columns}
        HAVING COUNT(*) > 1
        """,
        ACTIVE_STATUSES,
    ).fetchall()
    if not groups:
        return

    now = clock.to_db()
    for group in groups:
        scope_clause = "guild_id = ? AND user_id = ?" if has_guild else "user_id = ?"
        scope_params = (group["guild_id"], group["user_id"]) if has_guild else (group["user_id"],)
        rows = conn.execute(
            f"""
            SELECT id
            FROM tickets
            WHERE {scope_clause} AND status IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            (*scope_params, *ACTIVE_STATUSES),
        ).fetchall()
        duplicate_ids = [row["id"] for row in rows[1:]]
        if not duplicate_ids:
            continue
        id_placeholders = ",".join("?" for _ in duplicate_ids)
        conn.execute(
            f"""
            UPDATE tickets
            SET status = '{STATUS_CLOSED}',
                closed_at = COALESCE(closed_at, ?),
                reason = COALESCE(reason, 'closed by migration: duplicate open ticket')
            WHERE id IN ({id_placeholders})
            """,
            (now, *duplicate_ids),
        )


def _normalize_unknown_statuses(conn: Connection) -> None:
    """Значения статуса вне словаря мешают CHECK-ограничению при rebuild."""
    if not table_exists(conn, "tickets") or "status" not in columns_of(conn, "tickets"):
        return
    placeholders = ", ".join("?" for _ in ALL_STATUSES)
    conn.execute(
        f"UPDATE tickets SET status = '{STATUS_CLOSED}' "
        f"WHERE status IS NULL OR status NOT IN ({placeholders})",
        ALL_STATUSES,
    )


MIGRATIONS: tuple[tuple[int, Callable[[Connection], None]], ...] = (
    (1, _migration_1_initial_schema),
    (2, _migration_2_guild_scoped_data),
    (3, _migration_3_erasure_support),
    (4, _migration_4_utc_timestamps),
)


def migrate_schema(db_path: str | None = None) -> int:
    """Применяет миграции и возвращает итоговую версию схемы.

    Идемпотентна: повторный вызов на актуальной базе ничего не меняет.
    """
    target_db = db_path or config.DB_PATH
    conn = connect(target_db)
    try:
        current_version = _user_version(conn)
        if current_version > LATEST_SCHEMA_VERSION:
            raise SchemaError(
                f"база создана более новой версией бота (схема v{current_version}, "
                f"поддерживается v{LATEST_SCHEMA_VERSION}); обновите бота или "
                "восстановите базу из бэкапа — автоматическое понижение не выполняется"
            )
        if current_version == LATEST_SCHEMA_VERSION:
            problems = inspect_schema(conn)
            if not problems:
                return current_version
            logger.warning(
                "migrations: схема отмечена как актуальная, но структура расходится "
                f"с ожидаемой: {'; '.join(problems)}"
            )

        pending = [item for item in MIGRATIONS if item[0] > current_version]
        needs_repair = bool(inspect_schema(conn)) and current_version >= 1
        if not pending and not needs_repair:
            return current_version

        has_data = current_version > 0 and any(
            table_exists(conn, table.name) and _row_count(conn, table.name) for table in TABLES
        )
        if has_data:
            create_backup(conn, target_db)

        conn.execute("BEGIN IMMEDIATE")
        try:
            _normalize_unknown_statuses(conn)
            for version, migration in MIGRATIONS:
                if version <= current_version:
                    continue
                migration(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                current_version = version
            if needs_repair:
                for table in TABLES:
                    ensure_table(conn, table)
                conn.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
                current_version = LATEST_SCHEMA_VERSION
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

        problems = inspect_schema(conn)
        if problems:
            raise SchemaError(
                "после миграции структура базы всё ещё не соответствует ожидаемой: "
                f"{'; '.join(problems)}. База не тронута дальше — восстановите её "
                "из бэкапа рядом с файлом БД и сообщите разработчикам"
            )

        logger.info(f"migrations: схема базы приведена к версии v{current_version}")
        return current_version
    finally:
        conn.close()


def schema_version(db_path: str | None = None) -> int:
    conn = connect(db_path or config.DB_PATH)
    try:
        return _user_version(conn)
    finally:
        conn.close()
