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


class TestMigrationV3ErasureSupport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self._old_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(tickets_module)
        self.db = tickets_module
        self.db.init_db()

    def tearDown(self):
        config.DB_PATH = self._old_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_tickets_has_log_message_ids_column(self):
        conn = db_module.get_db()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertIn("log_message_ids", cols)
        self.assertGreaterEqual(version, 3)

    def test_bot_state_table_created(self):
        conn = db_module.get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_state'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_add_log_message_id_appends_refs(self):
        self.db.save_ticket(100, 42, "u", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=7)
        self.assertTrue(self.db.add_log_message_id(100, 900, 500))
        self.assertTrue(self.db.add_log_message_id(100, 901, 501))

        ticket = self.db.get_ticket(100)
        refs = self.db.parse_log_message_refs(ticket["log_message_ids"])
        self.assertEqual(refs, [(900, 500), (901, 501)])

    def test_add_log_message_id_unknown_ticket_returns_false(self):
        self.assertFalse(self.db.add_log_message_id(999, 900, 500))

    def test_parse_log_message_refs_tolerates_garbage(self):
        self.assertEqual(self.db.parse_log_message_refs(None), [])
        self.assertEqual(self.db.parse_log_message_refs("не-json"), [])
        self.assertEqual(self.db.parse_log_message_refs('["x", 1]'), [])
        self.assertEqual(self.db.parse_log_message_refs("[[1, 2]]"), [(1, 2)])


class TestAnonymizeClosesOpenTickets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self._old_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(tickets_module)
        self.db = tickets_module
        self.db.init_db()

    def tearDown(self):
        config.DB_PATH = self._old_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_open_ticket_becomes_closed_and_cleared(self):
        self.db.save_ticket(
            100, 42, "vasya", "T", "rp", '{"pii": 1}', "2024-01-01T00:00:00", guild_id=7
        )
        self.db.add_log_message_id(100, 900, 500)

        changed = self.db.anonymize_user_tickets(7, 42)

        self.assertEqual(changed, 1)
        ticket = self.db.get_ticket(100)
        self.assertEqual(ticket["status"], "closed")
        self.assertIsNotNone(ticket["closed_at"])
        self.assertEqual(ticket["user_id"], 0)
        self.assertEqual(ticket["answers"], "{}")
        self.assertIsNone(ticket["log_message_ids"])

    def test_user_can_apply_again_after_erasure(self):
        # анонимизированные строки не блокируют новую заявку того же человека
        self.db.save_ticket(100, 42, "vasya", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=7)
        self.db.anonymize_user_tickets(7, 42)
        self.db.save_ticket(200, 42, "vasya", "T2", "rp", "{}", "2024-01-02T00:00:00", guild_id=7)

        ticket = self.db.get_ticket(200)
        self.assertEqual(ticket["status"], "open")


class TestRetentionQueries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self._old_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(tickets_module)
        self.db = tickets_module
        self.db.init_db()

    def tearDown(self):
        config.DB_PATH = self._old_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def _set_closed_at(self, channel_id, closed_at):
        conn = db_module.get_db()
        try:
            conn.execute(
                "UPDATE tickets SET closed_at = ? WHERE channel_id = ?",
                (closed_at, channel_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_get_retention_expired_windows(self):
        cutoff = "2024-06-01T00:00:00"
        # давно закрыт — подлежит удалению
        self.db.save_ticket(100, 1, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=7)
        self.db.update_ticket_status(100, "accepted", 9, "ок")
        self._set_closed_at(100, "2024-01-15T00:00:00")
        # закрыт недавно (после границы) — остаётся
        self.db.save_ticket(101, 2, "b", "T", "rp", "{}", "2024-05-20T00:00:00", guild_id=7)
        self.db.update_ticket_status(101, "denied", 9, "нет")
        self._set_closed_at(101, "2024-06-05T00:00:00")
        # давно открыт (заброшен) — подлежит удалению
        self.db.save_ticket(102, 3, "c", "T", "rp", "{}", "2024-01-10T00:00:00", guild_id=7)

        expired = self.db.get_retention_expired(cutoff)
        expired_channels = {row["channel_id"] for row in expired}
        self.assertEqual(expired_channels, {100, 102})

    def test_delete_ticket_by_id(self):
        self.db.save_ticket(100, 1, "a", "T", "rp", "{}", "2024-01-01T00:00:00", guild_id=7)
        ticket = self.db.get_ticket(100)
        self.assertTrue(self.db.delete_ticket_by_id(ticket["id"]))
        self.assertIsNone(self.db.get_ticket(100))
        self.assertFalse(self.db.delete_ticket_by_id(ticket["id"]))


if __name__ == "__main__":
    unittest.main()
