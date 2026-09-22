import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from afk.events import setup_afk_events


class TestAfkEvents(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.bot.process_commands = AsyncMock()
        setup_afk_events(self.bot)
        self.on_message = self.bot.event.call_args_list[0][0][0]

    def tearDown(self):
        self.loop.close()

    def test_events_registered(self):
        # Только on_message: сообщения и голос не должны снимать AFK
        self.assertEqual(self.bot.event.call_count, 1)


class TestOnMessageEvent(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.bot.process_commands = AsyncMock()
        setup_afk_events(self.bot)
        self.on_message = self.bot.event.call_args_list[0][0][0]

    def tearDown(self):
        self.loop.close()

    def test_bot_message_ignored(self):
        message = MagicMock()
        message.author.bot = True
        self.loop.run_until_complete(self.on_message(message))
        self.bot.process_commands.assert_not_called()

    def test_no_guild_ignored(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = None
        self.loop.run_until_complete(self.on_message(message))
        # When guild is None, the function returns early without calling process_commands
        self.bot.process_commands.assert_not_called()

    def test_no_mentions(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.mentions = []

        self.loop.run_until_complete(self.on_message(message))
        self.bot.process_commands.assert_called_once()

    def test_mention_not_afk(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.author.id = 100
        message.mentions = [MagicMock()]
        message.mentions[0].id = 200
        message.channel = MagicMock()
        message.channel.send = AsyncMock()

        with patch("afk.events.get_afk_user") as mock_get:
            mock_get.return_value = None
            self.loop.run_until_complete(self.on_message(message))

        message.channel.send.assert_not_called()

    def test_mention_afk_with_reply(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.author.id = 100
        mention = MagicMock()
        mention.id = 200
        mention.mention = "<@200>"
        message.mentions = [mention]
        message.channel = MagicMock()
        message.channel.send = AsyncMock()

        mock_row = {"afk_since": "2024-01-01T10:00:00", "afk_reason": "test"}

        with patch("afk.events.get_afk_user") as mock_get:
            with patch("afk.events.check_and_reply") as mock_check:
                mock_get.return_value = mock_row
                mock_check.return_value = True
                self.loop.run_until_complete(self.on_message(message))

        message.channel.send.assert_called_once()

    def test_mention_afk_cooldown(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.author.id = 100
        mention = MagicMock()
        mention.id = 200
        message.mentions = [mention]
        message.channel = MagicMock()
        message.channel.send = AsyncMock()

        mock_row = {"afk_since": "2024-01-01T10:00:00", "afk_reason": "test"}

        with patch("afk.events.get_afk_user") as mock_get:
            with patch("afk.events.check_and_reply") as mock_check:
                mock_get.return_value = mock_row
                mock_check.return_value = False
                self.loop.run_until_complete(self.on_message(message))

        message.channel.send.assert_not_called()

    def test_multiple_mentions(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.author.id = 100
        mention1 = MagicMock()
        mention1.id = 200
        mention2 = MagicMock()
        mention2.id = 300
        message.mentions = [mention1, mention2]
        message.channel = MagicMock()
        message.channel.send = AsyncMock()

        mock_row = {"afk_since": "2024-01-01T10:00:00", "afk_reason": "test"}

        with patch("afk.events.get_afk_user") as mock_get:
            with patch("afk.events.check_and_reply") as mock_check:
                mock_get.return_value = mock_row
                mock_check.return_value = True
                self.loop.run_until_complete(self.on_message(message))

        self.assertEqual(message.channel.send.call_count, 2)

    def test_command_message_skips_afk_reply(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "!afk_check <@200>"
        message.author.id = 100
        mention = MagicMock()
        mention.id = 200
        message.mentions = [mention]
        message.channel = MagicMock()
        message.channel.send = AsyncMock()

        with patch("afk.events.get_afk_user") as mock_get:
            mock_get.return_value = {"afk_since": "2024-01-01T10:00:00", "afk_reason": "test"}
            self.loop.run_until_complete(self.on_message(message))

        # автоответа нет, но команда обрабатывается
        message.channel.send.assert_not_called()
        self.bot.process_commands.assert_called_once()


class TestActivityDoesNotRemoveAfk(unittest.TestCase):
    """По спецификации активность (чат, голос) НЕ снимает AFK:

    статус держится, пока не истечёт время или пока его не снимет
    модератор / сам пользователь через меню.
    """

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.bot.process_commands = AsyncMock()

    def tearDown(self):
        self.loop.close()

    def test_no_voice_handler_registered(self):
        setup_afk_events(self.bot)
        self.assertEqual(self.bot.event.call_count, 1)

    def test_message_from_afk_author_does_not_remove_afk(self):
        setup_afk_events(self.bot)
        on_message = self.bot.event.call_args_list[0][0][0]

        message = MagicMock()
        message.author.bot = False
        message.author.id = 200  # сам автор в AFK
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "привет"
        message.mentions = []

        with patch("afk.events.get_afk_user") as mock_get:
            self.loop.run_until_complete(on_message(message))

        # AFK автора даже не проверяется — активность в чате его не снимает
        mock_get.assert_not_called()
        self.bot.process_commands.assert_called_once()


if __name__ == "__main__":
    unittest.main()
