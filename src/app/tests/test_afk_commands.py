import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from afk.commands import AfkCog
from afk.models import format_duration
from tests.support import FakeGuild, FakeMember
from utils import clock


class TestFormatDuration(unittest.TestCase):
    def test_zero_seconds(self):
        result = format_duration(0)
        self.assertEqual(result, "0 сек")

    def test_seconds_only(self):
        result = format_duration(45)
        self.assertEqual(result, "45 сек")

    def test_minutes_only(self):
        result = format_duration(120)
        self.assertEqual(result, "2 мин")

    def test_hours_only(self):
        result = format_duration(7200)
        self.assertEqual(result, "2 ч")

    def test_hours_and_minutes(self):
        result = format_duration(7500)
        self.assertEqual(result, "2 ч 5 мин")

    def test_full_duration(self):
        result = format_duration(3661)
        self.assertEqual(result, "1 ч 1 мин 1 сек")

    def test_large_duration(self):
        result = format_duration(90061)
        self.assertEqual(result, "1 дн 1 ч 1 мин 1 сек")


class TestAfkCog(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

    def test_cog_name(self):
        self.assertEqual(self.cog.qualified_name, "AfkCog")

    def test_afk_command_exists(self):
        self.assertTrue(hasattr(self.cog, "afk_command"))

    def test_afk_list_command_exists(self):
        self.assertTrue(hasattr(self.cog, "afk_list_command"))

    def test_afk_check_command_exists(self):
        self.assertTrue(hasattr(self.cog, "afk_check_command"))

    def test_afk_stats_command_exists(self):
        self.assertTrue(hasattr(self.cog, "afk_stats_command"))

    def test_afk_remove_command_exists(self):
        self.assertTrue(hasattr(self.cog, "afk_remove_command"))


class AfkCommandTestCase(unittest.IsolatedAsyncioTestCase):
    """Поведение префиксных команд."""

    def setUp(self):
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)
        self.guild = FakeGuild(guild_id=123)
        self.ctx = MagicMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = self.guild
        self.ctx.author = FakeMember(user_id=999, name="author")
        self.ctx.author.roles = []

    @staticmethod
    def _embed(mock_send):
        call = mock_send.await_args
        return call.kwargs.get("embed") or call.args[0]

    async def test_afk_sends_menu_embed(self):
        await self.cog.afk_command.callback(self.cog, self.ctx)

        embed = self._embed(self.ctx.send)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("AFK", embed.title)

    async def test_afk_list_empty(self):
        with patch("afk.views.get_all_afk", return_value=[]):
            await self.cog.afk_list_command.callback(self.cog, self.ctx)

        embed = self._embed(self.ctx.send)
        self.assertIn("никого нет", embed.description.lower())

    async def test_afk_list_with_users(self):
        rows = [
            {
                "user_id": 111,
                "afk_reason": "test",
                "afk_since": clock.to_db(clock.shift(clock.utcnow(), hours=-1)),
                "estimated_return": None,
            }
        ]

        with patch("afk.views.get_all_afk", return_value=rows):
            await self.cog.afk_list_command.callback(self.cog, self.ctx)

        embed = self._embed(self.ctx.send)
        self.assertEqual(embed.fields[0].value, "1 человек")
        self.assertIn("<@111>", embed.description)

    async def test_afk_list_escapes_reason(self):
        rows = [
            {
                "user_id": 111,
                "afk_reason": "@everyone тревога",
                "afk_since": clock.to_db(clock.utcnow()),
                "estimated_return": None,
            }
        ]

        with patch("afk.views.get_all_afk", return_value=rows):
            await self.cog.afk_list_command.callback(self.cog, self.ctx)

        embed = self._embed(self.ctx.send)
        self.assertNotIn("@everyone", embed.description)

    async def test_afk_check_not_afk(self):
        member = FakeMember(user_id=456, name="TestUser")

        with patch("afk.commands.get_afk_user", return_value=None):
            await self.cog.afk_check_command.callback(self.cog, self.ctx, member)

        embed = self._embed(self.ctx.send)
        self.assertIn("не в AFK", embed.description)

    async def test_afk_check_user_is_afk(self):
        member = FakeMember(user_id=456, name="TestUser")
        row = {
            "afk_since": clock.to_db(clock.shift(clock.utcnow(), hours=-2)),
            "afk_reason": "test reason",
            "estimated_return": None,
        }

        with patch("afk.commands.get_afk_user", return_value=row):
            await self.cog.afk_check_command.callback(self.cog, self.ctx, member)

        embed = self._embed(self.ctx.send)
        self.assertIn("В АФК", embed.fields[0].value)

    async def test_afk_stats_no_data(self):
        member = FakeMember(user_id=456, name="TestUser")

        with patch("afk.commands.get_user_stats", return_value=None):
            await self.cog.afk_stats_command.callback(self.cog, self.ctx, member)

        embed = self._embed(self.ctx.send)
        self.assertIn("TestUser", embed.title)

    async def test_afk_stats_with_data(self):
        member = FakeMember(user_id=456, name="TestUser")
        stats = {
            "total_afk_count": 5,
            "total_afk_seconds": 3600,
            "longest_afk_seconds": 1800,
        }

        with patch("afk.commands.get_user_stats", return_value=stats):
            await self.cog.afk_stats_command.callback(self.cog, self.ctx, member)

        embed = self._embed(self.ctx.send)
        self.assertIn("Статистика", embed.title)


class AfkRemoveCommandTestCase(unittest.IsolatedAsyncioTestCase):
    """Модераторская команда !afk_remove: принудительное снятие AFK."""

    def setUp(self):
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)
        self.guild = FakeGuild(guild_id=123)
        self.ctx = MagicMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = self.guild
        self.ctx.author = FakeMember(user_id=999, name="mod")
        self.ctx.author.roles = []
        self.member = FakeMember(user_id=456, name="target")

    def _set_permissions(self, **flags):
        defaults = {"administrator": False, "manage_guild": False, "manage_messages": False}
        defaults.update(flags)
        self.ctx.author.guild_permissions = SimpleNamespace(**defaults)

    @staticmethod
    def _session(**overrides):
        session = {
            "user_id": 456,
            "duration_seconds": 3600,
            "original_nick": "Вася",
            "nick_applied": 1,
        }
        session.update(overrides)
        return session

    async def test_moderator_removes_afk(self):
        self._set_permissions(manage_messages=True)

        with patch("afk.commands.take_afk_session", return_value=self._session()) as mock_take:
            with patch("afk.commands.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                with patch("afk.commands.send_to_log", new_callable=AsyncMock) as mock_log:
                    await self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)

        mock_take.assert_called_once_with(456, 123)
        mock_nick.assert_awaited_once_with(self.member, "Вася", nick_applied=True)
        mock_log.assert_awaited_once()
        self.ctx.send.assert_awaited_once()

    async def test_member_without_afk(self):
        self._set_permissions(manage_messages=True)

        with patch("afk.commands.take_afk_session", return_value=None) as mock_take:
            with patch("afk.commands.send_to_log", new_callable=AsyncMock) as mock_log:
                await self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)

        mock_take.assert_called_once_with(456, 123)
        mock_log.assert_not_awaited()
        self.assertIn(config.AFK_CHECKED_NOT_AFK, self.ctx.send.await_args.args[0])

    async def test_regular_member_denied(self):
        self._set_permissions()

        with patch("afk.commands.take_afk_session") as mock_take:
            await self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)

        mock_take.assert_not_called()
        self.assertIn(config.AFK_NO_PERMISSION, self.ctx.send.await_args.args[0])

    async def test_staff_role_grants_access(self):
        self._set_permissions()
        role = MagicMock()
        role.id = 777
        self.ctx.author.roles = [role]

        with patch("utils.permissions.config.STAFF_ROLE_IDS", [777]):
            with patch("afk.commands.take_afk_session", return_value=self._session()) as mock_take:
                with patch("afk.commands.remove_afk_nickname", new_callable=AsyncMock):
                    with patch("afk.commands.send_to_log", new_callable=AsyncMock):
                        await self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)

        mock_take.assert_called_once_with(456, 123)

    async def test_nickname_not_restored_when_not_applied(self):
        self._set_permissions(manage_messages=True)
        session = self._session(nick_applied=0, original_nick=None)

        with patch("afk.commands.take_afk_session", return_value=session):
            with patch("afk.commands.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                with patch("afk.commands.send_to_log", new_callable=AsyncMock):
                    await self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)

        mock_nick.assert_awaited_once_with(self.member, None, nick_applied=False)


if __name__ == "__main__":
    unittest.main()
