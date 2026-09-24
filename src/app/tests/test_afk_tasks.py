"""Фоновое снятие AFK живёт на Cog, а не в module-global."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

import config
from afk.tasks import AfkExpiryCog, expire_afk_once, setup_expiry_loop
from tests.support import FakeGuild, FakeMember, http_exception, make_forbidden


class ExpireAfkTestCase(unittest.IsolatedAsyncioTestCase):
    """Один проход авто-снятия."""

    def setUp(self):
        patcher = patch("afk.tasks.cleanup_cooldowns", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)
        limiter = patch("afk.tasks.ratelimit.cleanup", return_value=0)
        limiter.start()
        self.addCleanup(limiter.stop)

    def _make_bot(self, member=None):
        guild = FakeGuild(guild_id=123, name="TestGuild")
        if member is not None:
            guild.add_member(member)
        bot = MagicMock()
        bot.guilds = [guild]
        return bot, guild

    @staticmethod
    def _session(**overrides):
        session = {
            "user_id": 456,
            "duration_seconds": 600,
            "original_nick": "Вася",
            "nick_applied": 1,
            "afk_reason": "обед",
        }
        session.update(overrides)
        return session

    async def test_expires_and_notifies(self):
        member = FakeMember(user_id=456, name="vasya")
        bot, guild = self._make_bot(member)

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]) as mock_expired:
            with patch("afk.tasks.take_afk_session", return_value=self._session()) as mock_take:
                with patch("afk.tasks.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                        count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_expired.assert_called_once()
        mock_take.assert_called_once_with(456, 123)
        mock_nick.assert_awaited_once_with(member, "Вася", nick_applied=True)
        member.send.assert_awaited_once()
        mock_log.assert_awaited_once()

    async def test_nothing_expired(self):
        bot, _ = self._make_bot()

        with patch("afk.tasks.get_expired_afk", return_value=[]):
            with patch("afk.tasks.take_afk_session") as mock_take:
                count = await expire_afk_once(bot)

        self.assertEqual(count, 0)
        mock_take.assert_not_called()

    async def test_member_left_guild_still_removed(self):
        bot, _ = self._make_bot()  # участника в кэше нет

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]):
            with patch("afk.tasks.take_afk_session", return_value=self._session()):
                with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                    count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_log.assert_awaited_once()

    async def test_row_taken_by_concurrent_actor_skipped(self):
        """Гонка с !afk_remove: снимает тот, кто первым забрал сессию."""
        bot, _ = self._make_bot()

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]):
            with patch("afk.tasks.take_afk_session", return_value=None):
                with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                    count = await expire_afk_once(bot)

        self.assertEqual(count, 0)
        mock_log.assert_not_awaited()

    async def test_db_error_does_not_stop_other_guilds(self):
        healthy = FakeGuild(guild_id=2, name="Healthy")
        broken = FakeGuild(guild_id=1, name="Broken")
        bot = MagicMock()
        bot.guilds = [broken, healthy]

        def expired(guild_id, _now):
            if guild_id == 1:
                raise RuntimeError("db down")
            return [{"user_id": 456}]

        with patch("afk.tasks.get_expired_afk", side_effect=expired):
            with patch("afk.tasks.take_afk_session", return_value=self._session()):
                with patch("afk.tasks.send_to_log", new_callable=AsyncMock):
                    count = await expire_afk_once(bot)

        self.assertEqual(count, 1)

    async def test_dm_forbidden_is_ignored(self):
        member = FakeMember(user_id=456)
        member.send = AsyncMock(side_effect=make_forbidden())
        bot, _ = self._make_bot(member)

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]):
            with patch("afk.tasks.take_afk_session", return_value=self._session()):
                with patch("afk.tasks.remove_afk_nickname", new_callable=AsyncMock):
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                        count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        mock_log.assert_awaited_once()

    async def test_nickname_restore_failure_does_not_abort(self):
        member = FakeMember(user_id=456)
        bot, _ = self._make_bot(member)

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]):
            with patch("afk.tasks.take_afk_session", return_value=self._session()):
                with patch(
                    "afk.tasks.remove_afk_nickname",
                    new_callable=AsyncMock,
                    side_effect=http_exception(),
                ):
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock) as mock_log:
                        count = await expire_afk_once(bot)

        self.assertEqual(count, 1)
        member.send.assert_awaited_once()
        mock_log.assert_awaited_once()

    async def test_nick_not_applied_passed_through(self):
        """Ник не ставился (нет прав) — восстановление не должно его трогать."""
        member = FakeMember(user_id=456)
        bot, _ = self._make_bot(member)
        session = self._session(nick_applied=0, original_nick=None)

        with patch("afk.tasks.get_expired_afk", return_value=[{"user_id": 456}]):
            with patch("afk.tasks.take_afk_session", return_value=session):
                with patch("afk.tasks.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                    with patch("afk.tasks.send_to_log", new_callable=AsyncMock):
                        await expire_afk_once(bot)

        mock_nick.assert_awaited_once_with(member, None, nick_applied=False)


class ExpiryCogLifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    """Старт/остановка/reload/несколько экземпляров бота."""

    async def asyncSetUp(self):
        self.bot = self._make_bot()

    @staticmethod
    async def _settle() -> None:
        """Отмена задачи применяется на следующем витке цикла событий."""
        for _ in range(3):
            await asyncio.sleep(0)

    def _make_bot(self) -> commands.Bot:
        """Настоящий Bot: у tasks.loop должен быть реальный жизненный цикл."""
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        # клиент не логинится в тестах, поэтому wait_until_ready подменяется:
        # без этого before_loop падает и задача завершается до проверки
        bot.wait_until_ready = AsyncMock()
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_setup_registers_cog_and_starts_task(self):
        cog = await setup_expiry_loop(self.bot)

        self.assertIsInstance(cog, AfkExpiryCog)
        self.assertTrue(cog.expiry_loop.is_running())
        self.assertIs(self.bot.get_cog("AfkExpiryCog"), cog)

    async def test_setup_is_idempotent(self):
        first = await setup_expiry_loop(self.bot)
        second = await setup_expiry_loop(self.bot)

        self.assertIs(first, second)
        self.assertTrue(first.expiry_loop.is_running())

    async def test_cog_unload_cancels_task(self):
        cog = await setup_expiry_loop(self.bot)

        await self.bot.remove_cog("AfkExpiryCog")
        await self._settle()

        self.assertFalse(cog.expiry_loop.is_running())
        self.assertIsNone(self.bot.get_cog("AfkExpiryCog"))

    async def test_reload_leaves_single_running_task(self):
        first = await setup_expiry_loop(self.bot)
        await self.bot.remove_cog("AfkExpiryCog")
        await self._settle()
        second = await setup_expiry_loop(self.bot)

        self.assertIsNot(first, second)
        self.assertFalse(first.expiry_loop.is_running())
        self.assertTrue(second.expiry_loop.is_running())

    async def test_two_bot_instances_have_independent_tasks(self):
        """Задача на экземпляре: второй бот не наследует состояние первого."""
        other = self._make_bot()

        first = await setup_expiry_loop(self.bot)
        second = await setup_expiry_loop(other)

        self.assertIsNot(first, second)
        self.assertIsNot(first.expiry_loop, second.expiry_loop)

        await self.bot.remove_cog("AfkExpiryCog")
        await self._settle()

        self.assertFalse(first.expiry_loop.is_running())
        self.assertTrue(second.expiry_loop.is_running())

    async def test_interval_comes_from_config(self):
        cog = await setup_expiry_loop(self.bot)

        self.assertEqual(cog.expiry_loop.seconds, config.AFK_EXPIRY_CHECK_SECONDS)


if __name__ == "__main__":
    unittest.main()
