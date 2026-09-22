import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from afk.commands import AfkCog
from afk.models import format_duration


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


class TestAfkCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_afk_sends_embed(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        self.loop.run_until_complete(self.cog.afk_command.callback(self.cog, ctx))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("AFK", embed.title)


class TestAfkListCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_afk_list_empty(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        with patch("afk.models.get_all_afk") as mock_get:
            mock_get.return_value = []
            self.loop.run_until_complete(self.cog.afk_list_command.callback(self.cog, ctx))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIn("никого нет", embed.description.lower())

    def test_afk_list_with_users(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123
        ctx.guild.get_member = MagicMock(return_value=None)

        mock_rows = [
            {
                "user_id": 111,
                "afk_reason": "test",
                "afk_since": "2024-01-01T10:00:00",
                "estimated_return": None,
            },
        ]

        with patch("afk.models.get_all_afk") as mock_get:
            mock_get.return_value = mock_rows
            self.loop.run_until_complete(self.cog.afk_list_command.callback(self.cog, ctx))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertEqual(embed.fields[0].value, "1 человек")
        self.assertIn("<@111>", embed.description)


class TestAfkCheckCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_afk_check_not_afk(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        member = MagicMock()
        member.id = 456
        member.display_name = "TestUser"

        with patch("afk.commands.get_afk_user") as mock_get:
            mock_get.return_value = None
            self.loop.run_until_complete(self.cog.afk_check_command.callback(self.cog, ctx, member))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIn("не в AFK", embed.description)

    def test_afk_check_user_is_afk(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        member = MagicMock()
        member.id = 456
        member.display_name = "TestUser"

        mock_row = {
            "afk_since": "2024-01-01T10:00:00",
            "afk_reason": "test reason",
        }

        with patch("afk.commands.get_afk_user") as mock_get:
            mock_get.return_value = mock_row
            self.loop.run_until_complete(self.cog.afk_check_command.callback(self.cog, ctx, member))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIn("В АФК", embed.fields[0].value)


class TestAfkStatsCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_afk_stats_no_data(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        member = MagicMock()
        member.id = 456
        member.display_name = "TestUser"

        with patch("afk.commands.get_user_stats") as mock_get:
            mock_get.return_value = None
            self.loop.run_until_complete(self.cog.afk_stats_command.callback(self.cog, ctx, member))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIn("TestUser", embed.title)

    def test_afk_stats_with_data(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        member = MagicMock()
        member.id = 456
        member.display_name = "TestUser"

        mock_stats = {
            "total_afk_count": 5,
            "total_afk_seconds": 3600,
            "longest_afk_seconds": 1800,
        }

        with patch("afk.commands.get_user_stats") as mock_get:
            mock_get.return_value = mock_stats
            self.loop.run_until_complete(self.cog.afk_stats_command.callback(self.cog, ctx, member))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIn("Статистика", embed.title)


class TestAfkRemoveCommand(unittest.TestCase):
    """Модераторская команда !afk_remove: принудительное снятие AFK."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = AfkCog(self.bot)

        self.ctx = MagicMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = MagicMock()
        self.ctx.guild.id = 123
        self.ctx.author.mention = "<@999>"
        self.ctx.author.roles = []

        self.member = MagicMock()
        self.member.id = 456
        self.member.mention = "<@456>"

    def tearDown(self):
        self.loop.close()

    def _set_permissions(self, **flags):
        defaults = {"administrator": False, "manage_guild": False, "manage_messages": False}
        defaults.update(flags)
        self.ctx.author.guild_permissions = SimpleNamespace(**defaults)

    def test_moderator_removes_afk(self):
        self._set_permissions(manage_messages=True)

        with patch("afk.commands.get_afk_user", return_value=None):
            with patch("afk.commands.remove_afk", return_value=3600) as mock_remove:
                with patch("afk.commands.remove_afk_nickname", new_callable=AsyncMock):
                    with patch("afk.commands.send_to_log", new_callable=AsyncMock) as mock_log:
                        self.loop.run_until_complete(
                            self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)
                        )

        mock_remove.assert_called_once_with(456, 123)
        mock_log.assert_called_once()
        self.ctx.send.assert_called_once()

    def test_member_without_afk(self):
        self._set_permissions(manage_messages=True)

        with patch("afk.commands.get_afk_user", return_value=None):
            with patch("afk.commands.remove_afk") as mock_remove:
                mock_remove.return_value = None
                self.loop.run_until_complete(
                    self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)
                )

        mock_remove.assert_called_once_with(456, 123)
        self.ctx.send.assert_called_once()

    def test_regular_member_denied(self):
        self._set_permissions()

        with patch("afk.commands.remove_afk") as mock_remove:
            self.loop.run_until_complete(
                self.cog.afk_remove_command.callback(self.cog, self.ctx, self.member)
            )

        mock_remove.assert_not_called()
        self.ctx.send.assert_called_once()

    def test_staff_role_grants_access(self):
        self._set_permissions()
        role = MagicMock()
        role.id = 777
        self.ctx.author.roles = [role]

        # is_staff читает config.STAFF_ROLE_IDS через utils.permissions
        with patch("utils.permissions.config.STAFF_ROLE_IDS", [777]):
            with patch("afk.commands.get_afk_user", return_value=None):
                with patch("afk.commands.remove_afk", return_value=60) as mock_remove:
                    with patch("afk.commands.remove_afk_nickname", new_callable=AsyncMock):
                        with patch("afk.commands.send_to_log", new_callable=AsyncMock):
                            self.loop.run_until_complete(
                                self.cog.afk_remove_command.callback(
                                    self.cog, self.ctx, self.member
                                )
                            )

        mock_remove.assert_called_once_with(456, 123)


if __name__ == "__main__":
    unittest.main()
