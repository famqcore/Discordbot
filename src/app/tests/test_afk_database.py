import importlib
import os
import tempfile
import unittest

import config
import database.afk_db as afk_module
import database.db as db_module


class TestAfkDatabase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(afk_module)
        self.db = afk_module
        self.db.init_afk_db()

    def tearDown(self):
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_init_afk_db_creates_tables(self):
        conn = db_module.get_db()
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row["name"] for row in c.fetchall()]
        conn.close()
        self.assertIn("afk_users", tables)
        self.assertIn("afk_cooldown", tables)
        self.assertIn("afk_stats", tables)

    def test_set_afk_basic(self):
        result = self.db.set_afk(123, 456, "test reason", "2024-01-01T00:00:00")
        self.assertTrue(result.created)
        self.assertFalse(result.updated)
        user = self.db.get_afk_user(123, 456)
        self.assertIsNotNone(user)
        self.assertEqual(user["user_id"], 123)
        self.assertEqual(user["guild_id"], 456)
        self.assertEqual(user["afk_reason"], "test reason")
        self.assertEqual(user["is_afk"], 1)

    def test_set_afk_with_return_time(self):
        self.db.set_afk(123, 456, "reason", "2024-01-01T00:00:00", "2024-01-01T12:00:00")
        user = self.db.get_afk_user(123, 456)
        self.assertIsNotNone(user)
        self.assertEqual(user["estimated_return"], "2024-01-01T12:00:00")

    def test_set_afk_update_existing(self):
        self.db.set_afk(123, 456, "reason1", "2024-01-01T00:00:00")
        result = self.db.set_afk(123, 456, "reason2", "2024-01-02T00:00:00")
        self.assertFalse(result.created)
        self.assertTrue(result.updated)
        user = self.db.get_afk_user(123, 456)
        self.assertEqual(user["afk_reason"], "reason2")

    def test_remove_afk(self):
        self.db.set_afk(123, 456, "reason", "2024-01-01T00:00:00")
        result = self.db.remove_afk(123, 456)
        self.assertTrue(result)
        user = self.db.get_afk_user(123, 456)
        self.assertIsNone(user)

    def test_remove_afk_not_exists(self):
        result = self.db.remove_afk(999, 999)
        self.assertFalse(result)

    def test_get_afk_user_not_found(self):
        user = self.db.get_afk_user(999, 999)
        self.assertIsNone(user)

    def test_get_all_afk_empty(self):
        rows = self.db.get_all_afk(456)
        self.assertEqual(len(rows), 0)

    def test_get_all_afk_with_data(self):
        self.db.set_afk(111, 456, "reason1", "2024-01-01T00:00:00")
        self.db.set_afk(222, 456, "reason2", "2024-01-02T00:00:00")
        self.db.set_afk(333, 789, "reason3", "2024-01-03T00:00:00")

        rows = self.db.get_all_afk(456)
        self.assertEqual(len(rows), 2)

    def test_get_all_afk_order(self):
        self.db.set_afk(111, 456, "reason1", "2024-01-02T00:00:00")
        self.db.set_afk(222, 456, "reason2", "2024-01-01T00:00:00")
        rows = self.db.get_all_afk(456)
        self.assertEqual(rows[0]["user_id"], 222)

    def test_check_cooldown_no_existing(self):
        result = self.db.check_cooldown(100, 200, 30)
        self.assertTrue(result)

    def test_check_cooldown_with_recent(self):
        self.db.set_cooldown(100, 200)
        result = self.db.check_cooldown(100, 200, 30)
        self.assertFalse(result)

    def test_set_cooldown_basic(self):
        self.db.set_cooldown(100, 200)
        # Сразу после установки кулдаун активен
        result = self.db.check_cooldown(100, 200, 30)
        self.assertFalse(result)
        # После истечения кулдауна
        conn = db_module.get_db()
        c = conn.cursor()
        past = (
            __import__("datetime").datetime.now() - __import__("datetime").timedelta(seconds=31)
        ).isoformat()
        c.execute(
            "UPDATE afk_cooldown SET last_reply = ? WHERE mentioner_id = 100 AND afk_user_id = 200",
            (past,),
        )
        conn.commit()
        conn.close()
        result = self.db.check_cooldown(100, 200, 30)
        self.assertTrue(result)

    def test_set_cooldown_update_existing(self):
        self.db.set_cooldown(100, 200)
        self.db.set_cooldown(100, 200)
        conn = db_module.get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM afk_cooldown WHERE mentioner_id = 100")
        row = c.fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_get_user_stats_not_found(self):
        stats = self.db.get_user_stats(999)
        self.assertIsNone(stats)

    def test_update_stats_on_set(self):
        self.db.update_stats_on_set(123)
        stats = self.db.get_user_stats(123)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["total_afk_count"], 1)

    def test_update_stats_on_set_increment(self):
        self.db.update_stats_on_set(123)
        self.db.update_stats_on_set(123)
        stats = self.db.get_user_stats(123)
        self.assertEqual(stats["total_afk_count"], 2)

    def test_update_stats_on_remove(self):
        self.db.update_stats_on_remove(123, 3600)
        stats = self.db.get_user_stats(123)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["total_afk_seconds"], 3600)
        self.assertEqual(stats["longest_afk_seconds"], 3600)

    def test_update_stats_on_remove_updates_longest(self):
        self.db.update_stats_on_remove(123, 1800)
        self.db.update_stats_on_remove(123, 3600)
        stats = self.db.get_user_stats(123)
        self.assertEqual(stats["total_afk_seconds"], 5400)
        self.assertEqual(stats["longest_afk_seconds"], 3600)

    def test_update_stats_on_remove_keeps_longest(self):
        self.db.update_stats_on_remove(123, 3600)
        self.db.update_stats_on_remove(123, 1800)
        stats = self.db.get_user_stats(123)
        self.assertEqual(stats["total_afk_seconds"], 5400)
        self.assertEqual(stats["longest_afk_seconds"], 3600)

    def test_afk_default_reason(self):
        self.db.set_afk(123, 456, "test", "2024-01-01T00:00:00")
        user = self.db.get_afk_user(123, 456)
        self.assertEqual(user["afk_reason"], "test")

    def test_afk_without_estimated_return(self):
        self.db.set_afk(123, 456, "reason", "2024-01-01T00:00:00")
        user = self.db.get_afk_user(123, 456)
        self.assertIsNone(user["estimated_return"])

    def test_set_afk_saves_original_nick(self):
        self.db.set_afk(123, 456, "reason", "2024-01-01T00:00:00", None, "Ориг Ник")
        user = self.db.get_afk_user(123, 456)
        self.assertEqual(user["original_nick"], "Ориг Ник")

    def test_set_afk_original_nick_default_none(self):
        self.db.set_afk(123, 456, "reason", "2024-01-01T00:00:00")
        user = self.db.get_afk_user(123, 456)
        self.assertIsNone(user["original_nick"])

    def test_cleanup_cooldowns_removes_old(self):
        self.db.set_cooldown(1, 2)
        conn = db_module.get_db()
        conn.execute("UPDATE afk_cooldown SET last_reply = '2020-01-01T00:00:00'")
        conn.commit()
        conn.close()

        removed = self.db.cleanup_cooldowns("2024-01-01T00:00:00")
        self.assertEqual(removed, 1)

    def test_cleanup_cooldowns_keeps_fresh(self):
        self.db.set_cooldown(1, 2)
        removed = self.db.cleanup_cooldowns("2020-01-01T00:00:00")
        self.assertEqual(removed, 0)

    def test_cooldown_is_guild_scoped(self):
        self.db.set_cooldown(100, 200, guild_id=1)
        self.assertFalse(self.db.check_cooldown(100, 200, 30, guild_id=1))
        self.assertTrue(self.db.check_cooldown(100, 200, 30, guild_id=2))

    def test_stats_are_guild_scoped(self):
        self.db.update_stats_on_set(123, guild_id=1)
        self.db.update_stats_on_set(123, guild_id=2)
        self.db.update_stats_on_remove(123, 60, guild_id=1)

        guild_one = self.db.get_user_stats(123, guild_id=1)
        guild_two = self.db.get_user_stats(123, guild_id=2)

        self.assertEqual(guild_one["total_afk_count"], 1)
        self.assertEqual(guild_one["total_afk_seconds"], 60)
        self.assertEqual(guild_two["total_afk_count"], 1)
        self.assertEqual(guild_two["total_afk_seconds"], 0)

    def test_delete_user_data_is_guild_scoped(self):
        self.db.set_afk(123, 1, "r", "2024-01-01T00:00:00")
        self.db.set_afk(123, 2, "r", "2024-01-01T00:00:00")
        self.db.update_stats_on_set(123, guild_id=1)
        self.db.update_stats_on_set(123, guild_id=2)
        self.db.set_cooldown(999, 123, guild_id=1)
        self.db.set_cooldown(999, 123, guild_id=2)

        counts = self.db.delete_user_data(123, 1)

        self.assertEqual(counts["afk_users"], 1)
        self.assertIsNone(self.db.get_afk_user(123, 1))
        self.assertIsNotNone(self.db.get_afk_user(123, 2))
        self.assertIsNone(self.db.get_user_stats(123, guild_id=1))
        self.assertIsNotNone(self.db.get_user_stats(123, guild_id=2))
        self.assertTrue(self.db.check_cooldown(999, 123, 30, guild_id=1))
        self.assertFalse(self.db.check_cooldown(999, 123, 30, guild_id=2))


class TestGetExpiredAfk(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(afk_module)
        self.db = afk_module
        self.db.init_afk_db()

    def tearDown(self):
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_returns_only_expired(self):
        self.db.set_afk(1, 456, "r", "2020-01-01T00:00:00", "2020-01-01T01:00:00")
        self.db.set_afk(2, 456, "r", "2020-01-01T00:00:00", "2099-01-01T00:00:00")
        rows = self.db.get_expired_afk(456, "2024-01-01T00:00:00")
        self.assertEqual([r["user_id"] for r in rows], [1])

    def test_skips_rows_without_return_time(self):
        self.db.set_afk(1, 456, "r", "2020-01-01T00:00:00")
        rows = self.db.get_expired_afk(456, "2024-01-01T00:00:00")
        self.assertEqual(rows, [])

    def test_isolated_by_guild(self):
        self.db.set_afk(1, 456, "r", "2020-01-01T00:00:00", "2020-01-01T01:00:00")
        self.db.set_afk(2, 789, "r", "2020-01-01T00:00:00", "2020-01-01T01:00:00")
        rows = self.db.get_expired_afk(456, "2024-01-01T00:00:00")
        self.assertEqual([r["user_id"] for r in rows], [1])

    def test_empty_when_nothing_afk(self):
        rows = self.db.get_expired_afk(456, "2024-01-01T00:00:00")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
