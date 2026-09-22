"""Issue #7: разрешение объектов строго по ID; имя — только opt-in dev-режим."""

import unittest
from unittest.mock import MagicMock, patch

import discord

from utils.resolve import get_category, get_role, get_voice_channel


def make_guild():
    guild = MagicMock()
    guild.id = 1
    guild.name = "Сервер"
    return guild


def make_role(role_id, name):
    role = MagicMock()
    role.id = role_id
    role.name = name
    return role


class TestGetRoleStrictMode(unittest.TestCase):
    """По умолчанию (production) поиска по имени нет."""

    def test_role_resolved_by_id(self):
        guild = make_guild()
        role = make_role(10, "Staff")
        guild.get_role = MagicMock(side_effect=lambda rid: role if rid == 10 else None)

        self.assertIs(get_role(guild, 10, "Admin"), role)

    def test_missing_role_returns_none_without_name_lookup(self):
        guild = make_guild()
        guild.get_role = MagicMock(return_value=None)
        # одноимённая роль существует — и всё равно не должна подменять ID
        guild.roles = [make_role(77, "Staff")]

        with patch("discord.utils.get") as mock_utils_get:
            self.assertIsNone(get_role(guild, 404, "Staff"))

        mock_utils_get.assert_not_called()

    def test_unset_id_returns_none_even_with_matching_name(self):
        guild = make_guild()
        guild.roles = [make_role(77, "Staff")]

        with patch("discord.utils.get") as mock_utils_get:
            self.assertIsNone(get_role(guild, None, "Staff"))

        mock_utils_get.assert_not_called()


class TestGetCategoryStrictMode(unittest.TestCase):
    def test_wrong_type_channel_rejected(self):
        guild = make_guild()
        voice = MagicMock(spec=discord.VoiceChannel)
        guild.get_channel = MagicMock(return_value=voice)

        self.assertIsNone(get_category(guild, 55, "Тикеты"))

    def test_category_by_id_accepted(self):
        guild = make_guild()
        category = MagicMock(spec=discord.CategoryChannel)
        guild.get_channel = MagicMock(return_value=category)

        self.assertIs(get_category(guild, 55, "Тикеты"), category)

    def test_no_name_lookup_by_default(self):
        guild = make_guild()
        guild.get_channel = MagicMock(return_value=None)

        with patch("discord.utils.get") as mock_utils_get:
            self.assertIsNone(get_category(guild, 55, "Тикеты"))
            self.assertIsNone(get_category(guild, None, "Тикеты"))

        mock_utils_get.assert_not_called()


class TestGetVoiceChannelStrictMode(unittest.TestCase):
    def test_text_channel_id_rejected(self):
        guild = make_guild()
        text = MagicMock(spec=discord.TextChannel)
        guild.get_channel = MagicMock(return_value=text)

        self.assertIsNone(get_voice_channel(guild, 88, "Обзвон"))

    def test_voice_channel_by_id_accepted(self):
        guild = make_guild()
        voice = MagicMock(spec=discord.VoiceChannel)
        guild.get_channel = MagicMock(return_value=voice)

        self.assertIs(get_voice_channel(guild, 88, "Обзвон"), voice)


class TestDevModeNameFallback(unittest.TestCase):
    """ALLOW_NAME_FALLBACK=true: старое поведение для локальной разработки."""

    def test_name_fallback_when_enabled(self):
        guild = make_guild()
        guild.get_role = MagicMock(return_value=None)
        role = make_role(77, "Staff")

        with patch("config.ALLOW_NAME_FALLBACK", True):
            with patch("discord.utils.get", return_value=role) as mock_utils_get:
                self.assertIs(get_role(guild, None, "Staff"), role)

        mock_utils_get.assert_called_once()

    def test_id_still_prioritized_in_dev_mode(self):
        guild = make_guild()
        role = make_role(10, "RealStaff")
        guild.get_role = MagicMock(return_value=role)

        with patch("config.ALLOW_NAME_FALLBACK", True):
            self.assertIs(get_role(guild, 10, "Staff"), role)

    def test_category_name_fallback_when_enabled(self):
        guild = make_guild()
        category = MagicMock(spec=discord.CategoryChannel)

        with patch("config.ALLOW_NAME_FALLBACK", True):
            with patch("discord.utils.get", return_value=category):
                self.assertIs(get_category(guild, None, "Тикеты"), category)

    def test_voice_name_fallback_when_enabled(self):
        guild = make_guild()
        voice = MagicMock(spec=discord.VoiceChannel)

        with patch("config.ALLOW_NAME_FALLBACK", True):
            with patch("discord.utils.get", return_value=voice):
                self.assertIs(get_voice_channel(guild, None, "Обзвон"), voice)


if __name__ == "__main__":
    unittest.main()
