"""Миграции схемы: preflight, backup, verified rebuild.

Фикстуры собирают базы в состояниях, которые встречаются у живых
установок: схема v1 без guild_id, частично применённая миграция,
дубликаты открытых заявок, наивные метки времени, «версия из будущего»
и база с повреждёнными ограничениями. Проверяется, что миграция либо
приводит базу к канону без потери строк, либо отказывается работать,
не оставляя базу в промежуточном состоянии.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import config
from database import db as db_module, migrations as migrations_module
from database.db import connect
from database.migrations import (
    LATEST_SCHEMA_VERSION,
    SchemaError,
    create_backup,
    inspect_schema,
    inspect_table,
    migrate_schema,
    primary_key_of,
    schema_version,
    table_exists,
    unique_sets_of,
)
from database.schema import TABLES, TABLES_BY_NAME
from utils import clock

# --------------------------------------------------------------------------
# фикстуры исторических схем
# --------------------------------------------------------------------------

SCHEMA_V1 = """
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER,
    user_id INTEGER,
    user_name TEXT,
    topic TEXT,
    type TEXT,
    answers TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT,
    closed_at TEXT,
    closed_by INTEGER,
    reason TEXT
);
CREATE TABLE stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    total_applications INTEGER DEFAULT 0,
    accepted INTEGER DEFAULT 0,
    denied INTEGER DEFAULT 0
);
CREATE TABLE afk_users (
    user_id INTEGER PRIMARY KEY,
    afk_reason TEXT,
    afk_since TEXT,
    estimated_return TEXT,
    original_nick TEXT
);
CREATE TABLE afk_cooldown (
    mentioner_id INTEGER,
    afk_user_id INTEGER,
    last_reply TEXT
);
CREATE TABLE afk_stats (
    user_id INTEGER PRIMARY KEY,
    total_afk_count INTEGER DEFAULT 0,
    total_afk_seconds INTEGER DEFAULT 0,
    longest_afk_seconds INTEGER DEFAULT 0
);
"""


class MigrationFixture(unittest.TestCase):
    """Временная база, на которой можно разложить любую историческую схему."""

    def setUp(self):
        # Миграции намеренно шумят в лог (бэкап, перестроение, отказ). В тестах
        # этот шум прячет настоящие ошибки — глушим на время теста.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        self._previous_path = config.DB_PATH
        db_module.shutdown()
        config.DB_PATH = self.db_path
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        db_module.shutdown()
        config.DB_PATH = self._previous_path
        directory = os.path.dirname(self.db_path) or "."
        base = os.path.basename(self.db_path)
        for name in os.listdir(directory):
            if name.startswith(base):
                try:
                    os.unlink(os.path.join(directory, name))
                except OSError:
                    pass

    def write_schema(self, script: str, version: int = 1) -> None:
        conn = connect(self.db_path)
        try:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(sql, params)
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def backup_files(self) -> list[str]:
        directory = os.path.dirname(self.db_path) or "."
        base = f"{os.path.basename(self.db_path)}.pre-migration."
        return [name for name in os.listdir(directory) if name.startswith(base)]


class FreshInstallTestCase(MigrationFixture):
    def test_creates_all_tables(self):
        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            for table in TABLES:
                self.assertTrue(table_exists(conn, table.name), table.name)
        finally:
            conn.close()

    def test_reaches_latest_version(self):
        self.assertEqual(migrate_schema(self.db_path), LATEST_SCHEMA_VERSION)

    def test_schema_matches_canon(self):
        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            self.assertEqual(inspect_schema(conn), [])
        finally:
            conn.close()

    def test_is_idempotent(self):
        migrate_schema(self.db_path)

        self.assertEqual(migrate_schema(self.db_path), LATEST_SCHEMA_VERSION)
        self.assertEqual(self.backup_files(), [])

    def test_no_backup_for_empty_database(self):
        migrate_schema(self.db_path)

        self.assertEqual(self.backup_files(), [])


class LegacySchemaBackupTestCase(MigrationFixture):
    """Данные в неверсированной legacy-БД нельзя перестраивать без бэкапа."""

    def setUp(self):
        super().setUp()
        self.write_schema(SCHEMA_V1, version=0)

    def test_backup_created_before_legacy_rebuild(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "Заявка", "open", "2024-01-01 10:00:00"),
        )

        migrate_schema(self.db_path)

        self.assertEqual(len(self.backup_files()), 1)


class UpgradeFromV1TestCase(MigrationFixture):
    """Схема v1: нет guild_id, нет CHECK, нет уникальных индексов."""

    def setUp(self):
        super().setUp()
        self.write_schema(SCHEMA_V1, version=1)

    def test_data_survives_upgrade(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "Заявка", "open", "2024-01-01 10:00:00"),
        )

        migrate_schema(self.db_path)

        rows = self.query("SELECT * FROM tickets")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic"], "Заявка")

    def test_guild_id_backfilled_with_default(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "Заявка", "open", "2024-01-01 10:00:00"),
        )

        migrate_schema(self.db_path)

        self.assertEqual(self.query("SELECT guild_id FROM tickets")[0]["guild_id"], 0)

    def test_primary_key_rebuilt_for_afk_users(self):
        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            self.assertEqual(set(primary_key_of(conn, "afk_users")), {"user_id", "guild_id"})
        finally:
            conn.close()

    def test_unique_constraints_created(self):
        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            self.assertIn(frozenset({"guild_id", "date"}), unique_sets_of(conn, "stats"))
        finally:
            conn.close()

    def test_backup_created_when_data_present(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "Заявка", "open", "2024-01-01 10:00:00"),
        )

        migrate_schema(self.db_path)

        self.assertEqual(len(self.backup_files()), 1)

    def test_backup_contains_pre_migration_data(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (100, 200, "Заявка", "open", "2024-01-01 10:00:00"),
        )

        migrate_schema(self.db_path)

        directory = os.path.dirname(self.db_path) or "."
        backup = os.path.join(directory, self.backup_files()[0])
        conn = connect(backup)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1)
        finally:
            conn.close()

    def test_row_counts_preserved_across_all_tables(self):
        self.execute("INSERT INTO stats (date, total_applications) VALUES ('2024-01-01', 5)")
        self.execute(
            "INSERT INTO afk_users (user_id, afk_since) VALUES (?, ?)",
            (7, "2024-01-01 10:00:00"),
        )
        self.execute(
            "INSERT INTO afk_cooldown (mentioner_id, afk_user_id, last_reply) VALUES (?, ?, ?)",
            (1, 2, "2024-01-01 10:00:00"),
        )
        self.execute("INSERT INTO afk_stats (user_id, total_afk_count) VALUES (9, 3)")

        migrate_schema(self.db_path)

        for table in ("stats", "afk_users", "afk_cooldown", "afk_stats"):
            with self.subTest(table=table):
                rows = self.query(f"SELECT COUNT(*) AS n FROM {table}")
                self.assertEqual(rows[0]["n"], 1)


class DuplicateTicketsTestCase(MigrationFixture):
    """Partial unique index нельзя создать поверх исторических дублей."""

    def setUp(self):
        super().setUp()
        self.write_schema(SCHEMA_V1, version=1)
        for channel_id, created in ((100, "2024-01-01 10:00:00"), (101, "2024-01-02 10:00:00")):
            self.execute(
                "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
                "VALUES (?, ?, ?, 'open', ?)",
                (channel_id, 200, "Заявка", created),
            )

    def test_migration_succeeds(self):
        self.assertEqual(migrate_schema(self.db_path), LATEST_SCHEMA_VERSION)

    def test_only_one_open_ticket_remains(self):
        migrate_schema(self.db_path)

        rows = self.query("SELECT channel_id FROM tickets WHERE status = 'open'")
        self.assertEqual(len(rows), 1)

    def test_newest_ticket_is_kept(self):
        migrate_schema(self.db_path)

        rows = self.query("SELECT channel_id FROM tickets WHERE status = 'open'")
        self.assertEqual(rows[0]["channel_id"], 101)

    def test_duplicates_are_closed_not_deleted(self):
        migrate_schema(self.db_path)

        rows = self.query("SELECT status, reason FROM tickets WHERE channel_id = 100")
        self.assertEqual(rows[0]["status"], "closed")
        self.assertIn("duplicate", rows[0]["reason"])

    def test_unique_index_is_enforced_afterwards(self):
        migrate_schema(self.db_path)

        with self.assertRaises(sqlite3.IntegrityError):
            self.execute(
                "INSERT INTO tickets (guild_id, channel_id, user_id, topic, status, created_at) "
                "VALUES (0, 999, 200, 'Ещё одна', 'open', '2024-03-01T00:00:00.000000+00:00')",
            )


class PartialMigrationTestCase(MigrationFixture):
    """База, помеченная актуальной, но недоделанная: preflight обязан заметить."""

    def setUp(self):
        super().setUp()
        migrate_schema(self.db_path)

    def test_missing_table_is_detected(self):
        self.execute("DROP TABLE bot_state")

        conn = connect(self.db_path)
        try:
            self.assertTrue(inspect_schema(conn))
        finally:
            conn.close()

    def test_missing_table_is_repaired(self):
        self.execute("DROP TABLE bot_state")

        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            self.assertTrue(table_exists(conn, "bot_state"))
        finally:
            conn.close()

    def test_dropped_index_is_recreated(self):
        self.execute("DROP INDEX idx_tickets_one_open_per_user")

        migrate_schema(self.db_path)

        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_tickets_one_open_per_user'"
        )
        self.assertEqual(len(rows), 1)

    def test_wrong_primary_key_is_rebuilt(self):
        self.execute("DROP TABLE afk_users")
        self.execute("CREATE TABLE afk_users (user_id INTEGER PRIMARY KEY, afk_since TEXT)")

        migrate_schema(self.db_path)

        conn = connect(self.db_path)
        try:
            self.assertEqual(inspect_table(conn, TABLES_BY_NAME["afk_users"]), [])
        finally:
            conn.close()

    def test_rebuild_keeps_rows(self):
        self.execute("DROP TABLE afk_users")
        self.execute("CREATE TABLE afk_users (user_id INTEGER PRIMARY KEY, afk_since TEXT)")
        self.execute("INSERT INTO afk_users (user_id, afk_since) VALUES (5, '2024-01-01 10:00:00')")

        migrate_schema(self.db_path)

        self.assertEqual(len(self.query("SELECT * FROM afk_users")), 1)

    def test_version_restored_after_repair(self):
        self.execute("DROP TABLE bot_state")

        migrate_schema(self.db_path)

        self.assertEqual(schema_version(self.db_path), LATEST_SCHEMA_VERSION)


class LegacyTimestampsTestCase(MigrationFixture):
    """Наивные метки трактуются как LEGACY_TIMEZONE и переводятся в UTC."""

    def setUp(self):
        super().setUp()
        self.write_schema(SCHEMA_V1, version=1)

    def test_naive_timestamp_normalized_to_utc(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'open', '2024-01-01 12:00:00')",
        )

        with patch.object(config, "LEGACY_TIMEZONE", "Europe/Moscow"):
            migrate_schema(self.db_path)

        stored = self.query("SELECT created_at FROM tickets")[0]["created_at"]
        self.assertEqual(clock.parse_db(stored), datetime(2024, 1, 1, 9, 0, tzinfo=UTC))

    def test_normalized_values_sort_chronologically(self):
        for channel_id, created in ((1, "2024-01-01 23:00:00"), (2, "2024-01-02 01:00:00")):
            self.execute(
                "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
                "VALUES (?, ?, 'Заявка', 'closed', ?)",
                (channel_id, channel_id, created),
            )

        with patch.object(config, "LEGACY_TIMEZONE", "Europe/Moscow"):
            migrate_schema(self.db_path)

        rows = self.query("SELECT channel_id FROM tickets ORDER BY created_at")
        self.assertEqual([row["channel_id"] for row in rows], [1, 2])

    def test_already_utc_values_untouched(self):
        value = "2024-01-01T12:00:00.000000+00:00"
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'open', ?)",
            (value,),
        )

        migrate_schema(self.db_path)

        self.assertEqual(self.query("SELECT created_at FROM tickets")[0]["created_at"], value)

    def test_broken_timestamp_left_as_is(self):
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'open', 'не дата')",
        )

        migrate_schema(self.db_path)

        self.assertEqual(self.query("SELECT created_at FROM tickets")[0]["created_at"], "не дата")


class UnknownStatusTestCase(MigrationFixture):
    def test_unknown_status_normalized_before_check_constraint(self):
        self.write_schema(SCHEMA_V1, version=1)
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'странный-статус', '2024-01-01 10:00:00')",
        )

        migrate_schema(self.db_path)

        self.assertEqual(self.query("SELECT status FROM tickets")[0]["status"], "closed")

    def test_null_status_normalized(self):
        self.write_schema(SCHEMA_V1, version=1)
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, created_at) "
            "VALUES (100, 200, 'Заявка', '2024-01-01 10:00:00')",
        )
        self.execute("UPDATE tickets SET status = NULL")

        migrate_schema(self.db_path)

        self.assertEqual(self.query("SELECT status FROM tickets")[0]["status"], "closed")


class RollbackTestCase(MigrationFixture):
    """Сбой посреди миграции не должен оставлять половину изменений."""

    def setUp(self):
        super().setUp()
        self.write_schema(SCHEMA_V1, version=1)
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'open', '2024-01-01 10:00:00')",
        )

    def test_failure_rolls_back_transaction(self):
        with patch(
            "database.migrations._normalize_datetime_column",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                migrate_schema(self.db_path)

        self.assertEqual(schema_version(self.db_path), 1)

    def test_data_intact_after_rollback(self):
        with patch(
            "database.migrations._normalize_datetime_column",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                migrate_schema(self.db_path)

        self.assertEqual(len(self.query("SELECT * FROM tickets")), 1)

    def test_backup_remains_after_failure(self):
        with patch(
            "database.migrations._normalize_datetime_column",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                migrate_schema(self.db_path)

        self.assertEqual(len(self.backup_files()), 1)

    @staticmethod
    def _rebuild_losing_rows():
        """Перестроение tickets, при котором замер «до» больше фактического.

        Счётчик подменяется только на время ``rebuild_table``: снаружи его
        вызывает ещё и решение о бэкапе, и подмена там сместила бы сценарий.
        """
        real_rebuild = migrations_module.rebuild_table
        real_row_count = migrations_module._row_count

        def rebuild(conn, table, *, reason):
            if table.name != "tickets":
                return real_rebuild(conn, table, reason=reason)

            inflated = {"done": False}

            def flaky_count(inner_conn, inner_table):
                actual = real_row_count(inner_conn, inner_table)
                if not inflated["done"]:
                    inflated["done"] = True
                    return actual + 1
                return actual

            with patch.object(migrations_module, "_row_count", flaky_count):
                return real_rebuild(conn, table, reason=reason)

        return rebuild

    def test_row_loss_aborts_migration(self):
        """Если перенос потеряет строки, миграция обязана отказаться."""
        with patch.object(migrations_module, "rebuild_table", self._rebuild_losing_rows()):
            with self.assertRaises(SchemaError) as ctx:
                migrate_schema(self.db_path)

        self.assertIn("потерял данные", str(ctx.exception))

    def test_row_loss_leaves_version_unchanged(self):
        with patch.object(migrations_module, "rebuild_table", self._rebuild_losing_rows()):
            with self.assertRaises(SchemaError):
                migrate_schema(self.db_path)

        self.assertEqual(schema_version(self.db_path), 1)

    def test_row_loss_keeps_original_data(self):
        with patch.object(migrations_module, "rebuild_table", self._rebuild_losing_rows()):
            with self.assertRaises(SchemaError):
                migrate_schema(self.db_path)

        rows = self.query("SELECT channel_id, topic FROM tickets")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic"], "Заявка")


class FutureVersionTestCase(MigrationFixture):
    def test_newer_schema_is_refused(self):
        migrate_schema(self.db_path)
        self.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION + 1}")

        with self.assertRaises(SchemaError) as ctx:
            migrate_schema(self.db_path)

        self.assertIn("более новой версией", str(ctx.exception))

    def test_refusal_does_not_touch_data(self):
        migrate_schema(self.db_path)
        self.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, topic, status, created_at) "
            "VALUES (0, 100, 200, 'Заявка', 'open', '2024-01-01T10:00:00.000000+00:00')",
        )
        self.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION + 1}")

        with self.assertRaises(SchemaError):
            migrate_schema(self.db_path)

        self.assertEqual(len(self.query("SELECT * FROM tickets")), 1)


class BackupFailureTestCase(MigrationFixture):
    def test_backup_error_aborts_migration(self):
        self.write_schema(SCHEMA_V1, version=1)
        self.execute(
            "INSERT INTO tickets (channel_id, user_id, topic, status, created_at) "
            "VALUES (100, 200, 'Заявка', 'open', '2024-01-01 10:00:00')",
        )

        with patch(
            "database.migrations.connect",
            side_effect=[connect(self.db_path), OSError("нет места")],
        ):
            with self.assertRaises(SchemaError) as ctx:
                migrate_schema(self.db_path)

        self.assertIn("резервную копию", str(ctx.exception))

    def test_in_memory_database_has_no_backup(self):
        conn = connect(":memory:")
        try:
            self.assertIsNone(create_backup(conn, ":memory:"))
        finally:
            conn.close()

    def test_missing_file_has_no_backup(self):
        conn = connect(self.db_path)
        try:
            self.assertIsNone(create_backup(conn, "/nonexistent/path/bot.db"))
        finally:
            conn.close()


class BackupRotationTestCase(MigrationFixture):
    def test_old_backups_are_pruned(self):
        migrate_schema(self.db_path)
        self.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, topic, status, created_at) "
            "VALUES (0, 100, 200, 'Заявка', 'open', '2024-01-01T10:00:00.000000+00:00')",
        )
        directory = os.path.dirname(self.db_path) or "."
        base = os.path.basename(self.db_path)
        for index in range(8):
            path = os.path.join(directory, f"{base}.pre-migration.2024010{index}-000000")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("stale")

        conn = connect(self.db_path)
        try:
            create_backup(conn, self.db_path)
        finally:
            conn.close()

        self.assertLessEqual(len(self.backup_files()), 5)


if __name__ == "__main__":
    unittest.main()
