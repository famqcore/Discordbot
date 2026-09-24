"""Проверка инфраструктуры на старте — fail fast в production."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from utils import startup
from utils.startup import check_startup, validate_guild_infrastructure


def make_config(**overrides):
    base = {
        "ROLE_RECRUITER_ID": None,
        "ROLE_OWNER_ID": None,
        "ROLE_DEP_OWNER_ID": None,
        "ROLE_ADMIN_ID": None,
        "ROLE_SUPPORT_ID": None,
        "ROLE_APPLIED_ID": None,
        "TICKETS_CATEGORY_ID": None,
        "VOICE_CHANNEL_IDS": [],
        "ALLOW_NAME_FALLBACK": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_guild():
    guild = MagicMock()
    guild.id = 1
    guild.name = "Сервер"
    guild.get_role = MagicMock(return_value=None)
    guild.get_channel = MagicMock(return_value=None)
    return guild


class TestValidateGuildInfrastructure(unittest.IsolatedAsyncioTestCase):
    async def _run(self, guild, config):
        with patch.object(startup, "config", config):
            with patch("utils.startup.validate_log_center_config", new_callable=AsyncMock) as m:
                m.return_value = []
                return await validate_guild_infrastructure(guild)

    async def test_empty_config_has_no_problems(self):
        self.assertEqual(await self._run(make_guild(), make_config()), [])

    async def test_missing_role_reported(self):
        guild = make_guild()
        problems = await self._run(guild, make_config(ROLE_RECRUITER_ID=10))
        self.assertTrue(any("ROLE_RECRUITER_ID" in p for p in problems))

    async def test_existing_role_accepted(self):
        guild = make_guild()
        role = MagicMock()
        guild.get_role = MagicMock(side_effect=lambda rid: role if rid == 10 else None)
        problems = await self._run(guild, make_config(ROLE_RECRUITER_ID=10, ROLE_OWNER_ID=20))
        self.assertFalse(any("ROLE_RECRUITER_ID" in p for p in problems))
        self.assertTrue(any("ROLE_OWNER_ID" in p for p in problems))

    async def test_category_must_be_category(self):
        guild = make_guild()
        text = MagicMock(spec=discord.TextChannel)
        guild.get_channel = MagicMock(return_value=text)
        problems = await self._run(guild, make_config(TICKETS_CATEGORY_ID=55))
        self.assertTrue(any("TICKETS_CATEGORY_ID" in p for p in problems))

    async def test_category_accepted(self):
        guild = make_guild()
        category = MagicMock(spec=discord.CategoryChannel)
        guild.get_channel = MagicMock(return_value=category)
        problems = await self._run(guild, make_config(TICKETS_CATEGORY_ID=55))
        self.assertEqual(problems, [])

    async def test_voice_channel_wrong_type_reported(self):
        guild = make_guild()
        text = MagicMock(spec=discord.TextChannel)
        guild.get_channel = MagicMock(return_value=text)
        problems = await self._run(guild, make_config(VOICE_CHANNEL_IDS=[88]))
        self.assertTrue(any("VOICE_CHANNEL_IDS" in p for p in problems))

    async def test_log_center_problems_join_report(self):
        guild = make_guild()
        with patch.object(startup, "config", make_config()):
            with patch(
                "utils.startup.validate_log_center_config", new_callable=AsyncMock
            ) as mock_log:
                mock_log.return_value = ["LOG_CHANNEL_ID=5: @everyone видит канал"]
                problems = await validate_guild_infrastructure(guild)
        self.assertEqual(problems, ["LOG_CHANNEL_ID=5: @everyone видит канал"])


class TestCheckStartup(unittest.IsolatedAsyncioTestCase):
    async def test_clean_config_passes(self):
        bot = MagicMock()
        bot.guilds = []
        bot.close = AsyncMock()

        with patch.object(startup, "config", make_config()):
            result = await check_startup(bot)

        self.assertTrue(result)
        bot.close.assert_not_called()

    async def test_problems_shut_bot_down_in_production(self):
        guild = make_guild()
        bot = MagicMock()
        bot.guilds = [guild]
        bot.close = AsyncMock()
        bot.startup_failed = False

        with patch.object(startup, "config", make_config(ROLE_RECRUITER_ID=10)):
            with patch(
                "utils.startup.validate_log_center_config", new_callable=AsyncMock
            ) as mock_log:
                mock_log.return_value = []
                result = await check_startup(bot)

        self.assertFalse(result)
        self.assertTrue(bot.startup_failed)
        bot.close.assert_awaited_once()

    async def test_problems_are_warnings_in_dev_mode(self):
        guild = make_guild()
        bot = MagicMock()
        bot.guilds = [guild]
        bot.close = AsyncMock()
        bot.startup_failed = False

        with patch.object(
            startup, "config", make_config(ALLOW_NAME_FALLBACK=True, ROLE_RECRUITER_ID=10)
        ):
            with patch(
                "utils.startup.validate_log_center_config", new_callable=AsyncMock
            ) as mock_log:
                mock_log.return_value = []
                result = await check_startup(bot)

        self.assertTrue(result)
        self.assertFalse(bot.startup_failed)
        bot.close.assert_not_called()

    async def test_multiple_guilds_all_checked(self):
        guild_a = make_guild()
        guild_b = make_guild()
        role = MagicMock()
        guild_b.get_role = MagicMock(return_value=role)
        bot = MagicMock()
        bot.guilds = [guild_a, guild_b]
        bot.close = AsyncMock()
        bot.startup_failed = False

        with patch.object(startup, "config", make_config(ROLE_ADMIN_ID=30)):
            with patch(
                "utils.startup.validate_log_center_config", new_callable=AsyncMock
            ) as mock_log:
                mock_log.return_value = []
                result = await check_startup(bot)

        # второй сервер чист, первый — нет: проверка идёт по всем серверам
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
