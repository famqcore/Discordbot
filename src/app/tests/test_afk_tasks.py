import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from afk.tasks import expire_afk_once, start_expiry_loop


class TestExpireAfkOnce(unittest.IsolatedAsyncioTestCase):
    """Фоновая задача: авто-снятие AFK по истечении времени."""

    def setUp(self):
        # чистка кулдаунов внутри expire_afk_once не должна ходить в реальную БД
        patcher = patch("afk.tasks.cleanup_cooldowns", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_bot(self):
        bot = MagicMock()
        guild = MagicMock()
        guild.id = 123
        guild.name = "TestGuild"
        bot.guilds = [guild]
        return bot, guild

    async def test_expires_and_notifies(self):
        bot, guild = self._make_bot()
        member = MagicMock()
        member.send = AsyncMock()
        guild.get_member = MagicMock(return_value=member)

        row = {"user_id": 456, "afk_reason": "test", "afk_since": "2020-01-01T00:00:00"}

        with patch("afk.tasks.get_expired_afk", return_value=[row]) as mock_expired:
            with patch("afk.tasks.remove_afk", return_value=600) as mock_remove:
                with patch("afk.tasks.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                        count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_expired.assert_called_once()
        mock_remove.assert_called_once_with(456, 123)
        mock_nick.assert_awaited_once_with(member, None)
        member.send.assert_awaited_once()
        mock_log.assert_awaited_once()

    async def test_nothing_expired(self):
        bot, _ = self._make_bot()

        with patch("afk.tasks.get_expired_afk", return_value=[]):
            with patch("afk.tasks.remove_afk") as mock_remove:
                count = await expire_afk_once(bot)

        self.assertEqual(count, 0)
        mock_remove.assert_not_called()

    async def test_member_left_guild_still_removed(self):
        bot, guild = self._make_bot()
        guild.get_member = MagicMock(return_value=None)

        row = {"user_id": 456, "afk_reason": "test", "afk_since": "2020-01-01T00:00:00"}

        with patch("afk.tasks.get_expired_afk", return_value=[row]):
            with patch("afk.tasks.remove_afk", return_value=600) as mock_remove:
                with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                    count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_remove.assert_called_once_with(456, 123)
        mock_log.assert_awaited_once()

    async def test_already_removed_row_skipped(self):
        bot, guild = self._make_bot()
        guild.get_member = MagicMock(return_value=None)

        row = {"user_id": 456, "afk_reason": "test", "afk_since": "2020-01-01T00:00:00"}

        with patch("afk.tasks.get_expired_afk", return_value=[row]):
            with patch("afk.tasks.remove_afk", return_value=None):
                with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                    count = await expire_afk_once(bot)

        self.assertEqual(count, 0)
        mock_log.assert_not_awaited()

    async def test_db_error_does_not_crash(self):
        bot, _ = self._make_bot()

        with patch("afk.tasks.get_expired_afk", side_effect=Exception("db down")):
            count = await expire_afk_once(bot)

        self.assertEqual(count, 0)

    async def test_dm_forbidden_is_ignored(self):
        bot, guild = self._make_bot()
        member = MagicMock()
        member.send = AsyncMock(side_effect=Exception("forbidden"))
        guild.get_member = MagicMock(return_value=member)

        row = {"user_id": 456, "afk_reason": "test", "afk_since": "2020-01-01T00:00:00"}

        with patch("afk.tasks.get_expired_afk", return_value=[row]):
            with patch("afk.tasks.remove_afk", return_value=600):
                with patch("afk.tasks.remove_afk_nickname", new_callable=AsyncMock):
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                        count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_log.assert_awaited_once()


class TestStartExpiryLoop(unittest.TestCase):
    def setUp(self):
        import afk.tasks as tasks_module

        self.module = tasks_module
        self.module._started = False

    def tearDown(self):
        self.module._started = False

    def test_starts_loop(self):
        bot = MagicMock()
        with patch("afk.tasks.tasks.loop") as mock_loop:
            decorator = MagicMock()
            loop_obj = MagicMock()
            decorator.return_value = loop_obj  # декоратор возвращает объект цикла
            mock_loop.return_value = decorator
            start_expiry_loop(bot)

        mock_loop.assert_called_once()
        loop_obj.start.assert_called_once()
        self.assertTrue(self.module._started)

    def test_does_not_start_twice(self):
        bot = MagicMock()
        with patch("afk.tasks.tasks.loop") as mock_loop:
            self.module._started = True  # имитируем уже запущенный цикл
            start_expiry_loop(bot)

        mock_loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
