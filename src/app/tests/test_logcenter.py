import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.logcenter import send_to_log


class TestSendToLog(unittest.IsolatedAsyncioTestCase):
    """send_to_log никогда не бросает исключений наружу."""

    async def test_none_guild_returns_false(self):
        result = await send_to_log(None, "afk", content="test")
        self.assertFalse(result)

    async def test_broken_guild_returns_false(self):
        # Любой сбой внутри (нет прав, MagicMock-структуры) — просто False
        guild = MagicMock()
        with patch("utils.logcenter.config") as mock_config:
            mock_config.LOG_CHANNEL_ID = None
            result = await send_to_log(guild, "afk", content="test")
        self.assertFalse(result)

    async def test_uses_existing_channel_by_id(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        thread = MagicMock()
        thread.send = AsyncMock()

        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=channel)
        guild.get_thread = MagicMock(return_value=thread)

        with patch("utils.logcenter.config") as mock_config:
            mock_config.LOG_CHANNEL_ID = 111
            mock_config.LOG_THREAD_IDS = {"afk": 222}
            mock_config.LOG_THREAD_NAMES = {"afk": "🔴-afk"}
            result = await send_to_log(guild, "afk", content="test")

        self.assertTrue(result)
        thread.send.assert_awaited_once()

    async def test_falls_back_to_channel_root(self):
        # Ветка падает (например, нет прав на создание), канал — доступен
        channel = MagicMock()
        channel.send = AsyncMock()
        channel.threads = []
        channel.create_thread = AsyncMock(side_effect=Exception("no perms"))

        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=channel)

        with patch("utils.logcenter.config") as mock_config:
            mock_config.LOG_CHANNEL_ID = 111
            mock_config.LOG_THREAD_IDS = {"afk": None}
            mock_config.LOG_THREAD_NAMES = {"afk": "🔴-afk"}
            result = await send_to_log(guild, "afk", content="test")

        self.assertTrue(result)
        channel.send.assert_awaited_once()

    async def test_creates_thread_when_missing(self):
        thread = MagicMock()
        thread.send = AsyncMock()

        channel = MagicMock()
        channel.send = AsyncMock()
        channel.threads = []
        channel.create_thread = AsyncMock(return_value=thread)

        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=channel)

        with patch("utils.logcenter.config") as mock_config:
            mock_config.LOG_CHANNEL_ID = 111
            mock_config.LOG_THREAD_IDS = {"afk": None}
            mock_config.LOG_THREAD_NAMES = {"afk": "🔴-afk"}
            result = await send_to_log(guild, "afk", content="test")

        self.assertTrue(result)
        channel.create_thread.assert_awaited_once()
        thread.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
