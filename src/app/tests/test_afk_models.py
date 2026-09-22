import importlib
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import afk.models as models_module
import config
import database.afk_db as afk_module
import database.db as db_module


class TestAfkModels(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        config.DB_PATH = self.temp.name
        importlib.reload(db_module)
        importlib.reload(afk_module)
        importlib.reload(models_module)
        self.models = models_module
        afk_module.init_afk_db()

    def tearDown(self):
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass

    def test_set_afk_basic(self):
        self.models.set_afk(123, 456, "test reason")
        user = self.models.get_afk_user(123, 456)
        self.assertIsNotNone(user)
        self.assertEqual(user["user_id"], 123)
        self.assertEqual(user["afk_reason"], "test reason")

    def test_set_afk_with_estimated_return(self):
        self.models.set_afk(123, 456, "reason", "2024-12-31T23:59:59")
        user = self.models.get_afk_user(123, 456)
        self.assertEqual(user["estimated_return"], "2024-12-31T23:59:59")

    def test_set_afk_updates_stats(self):
        self.models.set_afk(123, 456, "reason")
        stats = self.models.get_user_stats(123)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["total_afk_count"], 1)

    def test_set_afk_twice_counts_once(self):
        # обновление причины у уже стоящего AFK — не новый уход
        self.models.set_afk(123, 456, "reason")
        self.models.set_afk(123, 456, "reason2")
        stats = self.models.get_user_stats(123)
        self.assertEqual(stats["total_afk_count"], 1)

    def test_remove_afk_basic(self):
        self.models.set_afk(123, 456, "reason")
        duration = self.models.remove_afk(123, 456)
        self.assertIsNotNone(duration)
        self.assertIsInstance(duration, int)

    def test_remove_afk_not_found(self):
        duration = self.models.remove_afk(999, 999)
        self.assertIsNone(duration)

    def test_remove_afk_updates_stats(self):
        self.models.set_afk(123, 456, "reason")
        import time

        time.sleep(1)
        self.models.remove_afk(123, 456)
        stats = self.models.get_user_stats(123)
        self.assertIsNotNone(stats)
        self.assertGreaterEqual(stats["total_afk_seconds"], 0)

    def test_get_afk_user_not_found(self):
        user = self.models.get_afk_user(999, 999)
        self.assertIsNone(user)

    def test_get_afk_user_returns_dict(self):
        self.models.set_afk(123, 456, "reason")
        user = self.models.get_afk_user(123, 456)
        self.assertIsInstance(user, dict)

    def test_get_all_afk_empty(self):
        rows = self.models.get_all_afk(456)
        self.assertEqual(len(rows), 0)

    def test_get_all_afk_returns_list_of_dicts(self):
        self.models.set_afk(111, 456, "reason1")
        self.models.set_afk(222, 456, "reason2")
        rows = self.models.get_all_afk(456)
        self.assertEqual(len(rows), 2)
        self.assertIsInstance(rows[0], dict)

    def test_check_and_reply_first_time(self):
        result = self.models.check_and_reply(100, 200)
        self.assertTrue(result)

    def test_check_and_reply_cooldown(self):
        self.models.check_and_reply(100, 200)
        result = self.models.check_and_reply(100, 200)
        self.assertFalse(result)

    def test_check_and_reply_after_cooldown_expires(self):
        self.models.check_and_reply(100, 200)
        # Манипулируем временем кулдауна в БД
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
        result = self.models.check_and_reply(100, 200)
        self.assertTrue(result)

    def test_get_user_stats_not_found(self):
        stats = self.models.get_user_stats(999)
        self.assertIsNone(stats)

    def test_get_user_stats_returns_dict(self):
        self.models.set_afk(123, 456, "reason")
        stats = self.models.get_user_stats(123)
        self.assertIsInstance(stats, dict)

    def test_afk_stats_increment(self):
        self.models.set_afk(123, 456, "reason")
        self.models.remove_afk(123, 456)
        self.models.set_afk(123, 456, "reason2")
        self.models.remove_afk(123, 456)
        stats = self.models.get_user_stats(123)
        self.assertEqual(stats["total_afk_count"], 2)

    def test_afk_stats_total_time(self):
        self.models.set_afk(123, 456, "reason")
        self.models.remove_afk(123, 456)
        stats = self.models.get_user_stats(123)
        self.assertGreaterEqual(stats["total_afk_seconds"], 0)

    def test_multiple_users_same_guild(self):
        self.models.set_afk(111, 456, "reason1")
        self.models.set_afk(222, 456, "reason2")
        rows = self.models.get_all_afk(456)
        self.assertEqual(len(rows), 2)

    def test_same_user_different_guilds(self):
        self.models.set_afk(123, 456, "reason1")
        self.models.set_afk(123, 789, "reason2")
        user1 = self.models.get_afk_user(123, 456)
        user2 = self.models.get_afk_user(123, 789)
        self.assertEqual(user1["afk_reason"], "reason1")
        self.assertEqual(user2["afk_reason"], "reason2")

    def test_remove_only_one_guild(self):
        self.models.set_afk(123, 456, "reason1")
        self.models.set_afk(123, 789, "reason2")
        self.models.remove_afk(123, 456)
        user1 = self.models.get_afk_user(123, 456)
        user2 = self.models.get_afk_user(123, 789)
        self.assertIsNone(user1)
        self.assertIsNotNone(user2)


class TestAfkNickname(unittest.IsolatedAsyncioTestCase):
    async def test_add_afk_nickname_already_has_prefix(self):
        member = MagicMock()
        member.nick = "[AFK] TestUser"
        member.edit = AsyncMock()
        result = await models_module.add_afk_nickname(member)
        self.assertTrue(result)
        member.edit.assert_not_called()

    async def test_add_afk_nickname_no_nick(self):
        member = MagicMock()
        member.nick = None
        member.display_name = "TestUser"
        member.edit = AsyncMock()
        result = await models_module.add_afk_nickname(member)
        self.assertTrue(result)
        member.edit.assert_called_once()
        call_args = member.edit.call_args
        self.assertEqual(call_args.kwargs["nick"], "[AFK] TestUser")

    async def test_add_afk_nickname_forbidden(self):
        member = MagicMock()
        member.nick = None
        member.display_name = "TestUser"
        from discord.errors import Forbidden

        member.edit = AsyncMock(side_effect=Forbidden(MagicMock(), "Missing Permissions"))
        result = await models_module.add_afk_nickname(member)
        self.assertFalse(result)

    async def test_remove_afk_nickname_forbidden(self):
        member = MagicMock()
        member.nick = "[AFK] TestUser"
        from discord.errors import Forbidden

        member.edit = AsyncMock(side_effect=Forbidden(MagicMock(), "Missing Permissions"))
        result = await models_module.remove_afk_nickname(member)
        self.assertFalse(result)

    async def test_remove_afk_nickname_no_prefix(self):
        member = MagicMock()
        member.nick = "TestUser"
        member.edit = AsyncMock()
        result = await models_module.remove_afk_nickname(member)
        self.assertTrue(result)
        member.edit.assert_not_called()

    async def test_remove_afk_nickname_with_prefix(self):
        member = MagicMock()
        member.nick = "[AFK] TestUser"
        member.edit = AsyncMock()
        result = await models_module.remove_afk_nickname(member)
        self.assertTrue(result)
        member.edit.assert_called_once()

    async def test_remove_afk_nickname_only_prefix(self):
        member = MagicMock()
        member.nick = "[AFK] "
        member.edit = AsyncMock()
        result = await models_module.remove_afk_nickname(member)
        self.assertTrue(result)
        member.edit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
