import importlib
import os
import sqlite3
import tempfile
import unittest

import config
import database.db as db_module
import database.tickets_db as tickets_module


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(tickets_module)
        self.db = tickets_module
        self.db.init_db()

    def tearDown(self):
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_init_db_creates_tables(self):
        conn = db_module.get_db()
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row["name"] for row in c.fetchall()]
        conn.close()
        self.assertIn("tickets", tables)
        self.assertIn("stats", tables)

    def test_save_and_get_ticket(self):
        self.db.save_ticket(123, 456, "test_user", "RP ЗАЯВКА", "rp", "{}", "2024-01-01T00:00:00")
        ticket = self.db.get_ticket(123)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["channel_id"], 123)
        self.assertEqual(ticket["user_id"], 456)
        self.assertEqual(ticket["user_name"], "test_user")
        self.assertEqual(ticket["topic"], "RP ЗАЯВКА")
        self.assertEqual(ticket["type"], "rp")
        self.assertEqual(ticket["status"], "open")

    def test_get_ticket_not_found(self):
        ticket = self.db.get_ticket(99999)
        self.assertIsNone(ticket)

    def test_delete_ticket(self):
        self.db.save_ticket(111, 222, "user", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.delete_ticket(111)
        ticket = self.db.get_ticket(111)
        self.assertIsNone(ticket)

    def test_update_ticket_status_accepted(self):
        self.db.save_ticket(100, 200, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.update_ticket_status(100, "accepted", 300, "ok")
        ticket = self.db.get_ticket(100)
        self.assertEqual(ticket["status"], "accepted")
        self.assertEqual(ticket["closed_by"], 300)
        self.assertEqual(ticket["reason"], "ok")
        self.assertIsNotNone(ticket["closed_at"])

    def test_update_ticket_status_denied(self):
        self.db.save_ticket(101, 201, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.update_ticket_status(101, "denied", 301, "no")
        ticket = self.db.get_ticket(101)
        self.assertEqual(ticket["status"], "denied")

    def test_update_ticket_status_returns_true(self):
        self.db.save_ticket(102, 202, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.assertTrue(self.db.update_ticket_status(102, "accepted", 300, "ok"))

    def test_update_ticket_status_missing_ticket(self):
        self.assertFalse(self.db.update_ticket_status(999, "accepted", 300, "ok"))

    def test_update_ticket_status_twice_returns_false(self):
        self.db.save_ticket(103, 203, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.assertTrue(self.db.update_ticket_status(103, "accepted", 300, "ok"))
        self.assertFalse(self.db.update_ticket_status(103, "denied", 300, "no"))

    def test_update_ticket_status_twice_stats_not_doubled(self):
        self.db.save_ticket(104, 204, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.update_ticket_status(104, "accepted", 300, "ok")
        self.db.update_ticket_status(104, "accepted", 300, "ok")

        stats = self.db.get_stats()
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["denied"], 0)

    def test_update_ticket_status_closed_not_in_stats(self):
        self.db.save_ticket(105, 205, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.assertTrue(self.db.update_ticket_status(105, "closed", 300))

        stats = self.db.get_stats()
        self.assertEqual(stats["accepted"], 0)
        self.assertEqual(stats["denied"], 0)
        self.assertEqual(stats["open"], 0)

    def test_save_ticket_duplicate_channel_raises(self):
        self.db.save_ticket(106, 206, "u", "T", "rp", "{}", "2024-01-01T00:00:00")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.save_ticket(106, 207, "u2", "T", "rp", "{}", "2024-01-01T00:00:00")

    def test_stats_increment(self):
        self.db.save_ticket(1, 10, "a", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.update_ticket_status(1, "accepted", 99, "ok")
        self.db.save_ticket(2, 20, "b", "T", "rp", "{}", "2024-01-01T00:00:00")
        self.db.update_ticket_status(2, "denied", 99, "no")

        stats = self.db.get_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["denied"], 1)
        self.assertEqual(stats["open"], 0)
        self.assertEqual(len(stats["weekly"]), 1)

    def test_get_all_tickets_limit(self):
        for i in range(10):
            self.db.save_ticket(i, i, f"user{i}", "T", "rp", "{}", f"2024-01-{i + 1:02d}T00:00:00")
        results = self.db.get_all_tickets(limit=5)
        self.assertEqual(len(results), 5)

    def test_get_all_tickets_order(self):
        self.db.save_ticket(1, 1, "a", "T", "rp", "{}", "2024-01-02T00:00:00")
        self.db.save_ticket(2, 2, "b", "T", "rp", "{}", "2024-01-01T00:00:00")
        results = self.db.get_all_tickets(limit=10)
        self.assertEqual(results[0]["channel_id"], 1)

    def test_schema_version_is_recorded(self):
        conn = db_module.get_db()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(version, 2)

    def test_ticket_stats_are_guild_scoped(self):
        self.db.save_ticket(10, 100, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=1)
        self.db.save_ticket(20, 100, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=2)
        self.db.update_ticket_status(10, "accepted", 300, "ok")

        guild_one = self.db.get_stats(1)
        guild_two = self.db.get_stats(2)

        self.assertEqual(guild_one["total"], 1)
        self.assertEqual(guild_one["accepted"], 1)
        self.assertEqual(guild_two["total"], 1)
        self.assertEqual(guild_two["accepted"], 0)
        self.assertEqual(guild_two["open"], 1)

    def test_open_ticket_unique_per_user_and_guild(self):
        self.db.save_ticket(30, 100, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=1)
        self.db.save_ticket(31, 100, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=2)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.save_ticket(
                32,
                100,
                "a",
                "T",
                "rp",
                "{}",
                "2024-01-01T00:00:00",
                guild_id=1,
            )

    def test_anonymize_user_tickets_only_current_guild(self):
        self.db.save_ticket(
            40, 100, "a", "T", "rp", '{"name":"secret"}', "2024-01-01T00:00:00", guild_id=1
        )
        self.db.save_ticket(
            41, 100, "a", "T", "rp", '{"name":"secret"}', "2024-01-01T00:00:00", guild_id=2
        )

        changed = self.db.anonymize_user_tickets(1, 100)

        first = self.db.get_ticket(40)
        second = self.db.get_ticket(41)
        self.assertEqual(changed, 1)
        self.assertEqual(first["user_id"], 0)
        self.assertEqual(first["answers"], "{}")
        self.assertEqual(second["user_id"], 100)


class TestDatabaseMigrations(unittest.TestCase):
    def test_old_schema_migrates_without_losing_rows(self):
        temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp.close()
        old_path = config.DB_PATH
        config.DB_PATH = temp.name
        try:
            conn = sqlite3.connect(temp.name)
            conn.execute(
                """
                CREATE TABLE tickets (
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
                INSERT INTO tickets (channel_id, user_id, user_name, topic, type, answers, created_at, status)
                VALUES (900, 901, 'legacy', 'T', 'rp', '{}', '2024-01-01T00:00:00', 'open')
                """
            )
            conn.execute(
                """
                CREATE TABLE afk_stats (
                    user_id INTEGER PRIMARY KEY,
                    total_afk_count INTEGER DEFAULT 0,
                    total_afk_seconds INTEGER DEFAULT 0,
                    longest_afk_seconds INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO afk_stats (user_id, total_afk_count, total_afk_seconds, longest_afk_seconds)
                VALUES (901, 2, 30, 20)
                """
            )
            conn.commit()
            conn.close()

            import database.migrations as migrations

            importlib.reload(db_module)
            importlib.reload(migrations)
            migrations.migrate_schema()

            conn = db_module.get_db()
            ticket = conn.execute("SELECT * FROM tickets WHERE channel_id = 900").fetchone()
            stats = conn.execute("SELECT * FROM afk_stats WHERE user_id = 901").fetchone()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()

            self.assertEqual(ticket["guild_id"], 0)
            self.assertEqual(stats["guild_id"], 0)
            self.assertEqual(stats["total_afk_count"], 2)
            self.assertGreaterEqual(version, 2)
        finally:
            config.DB_PATH = old_path
            try:
                os.unlink(temp.name)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
