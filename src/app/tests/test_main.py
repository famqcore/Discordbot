import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

import config
import main as main_module


class TestSetupHook(unittest.IsolatedAsyncioTestCase):
    async def test_setup_hook_loads_everything(self):
        with patch("main.init_db") as mock_init:
            with patch("main.init_afk_db") as mock_afk_init:
                with patch.object(
                    main_module.bot, "load_extension", new_callable=AsyncMock
                ) as mock_load:
                    with patch.object(main_module.bot, "add_view") as mock_add_view:
                        await main_module.bot.setup_hook()

        mock_init.assert_called_once()
        mock_afk_init.assert_called_once()
        mock_load.assert_any_await("tickets")
        mock_load.assert_any_await("afk")
        self.assertEqual(mock_load.await_count, 2)
        # persistent views: заявки, кнопки тикета, AFK-меню
        self.assertEqual(mock_add_view.call_count, 3)

    async def test_on_ready_only_logs(self):
        # on_ready больше не грузит расширения и не трогает БД:
        # иначе при переподключении бот падал бы с ExtensionAlreadyLoaded
        with patch("main.init_db") as mock_init:
            with patch("main.logger") as mock_logger:
                await main_module.on_ready()

        mock_init.assert_not_called()
        mock_logger.info.assert_called_once()


class TestCommandError(unittest.IsolatedAsyncioTestCase):
    def _make_ctx(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = None
        ctx.command = "stats"
        return ctx

    async def test_command_not_found_silent(self):
        ctx = self._make_ctx()
        await main_module.on_command_error(ctx, commands.CommandNotFound())
        ctx.send.assert_not_called()

    async def test_no_private_message(self):
        ctx = self._make_ctx()
        await main_module.on_command_error(ctx, commands.NoPrivateMessage())
        ctx.send.assert_called_once()

    async def test_missing_argument_hint(self):
        ctx = self._make_ctx()
        error = commands.MissingRequiredArgument(MagicMock(name="member"))
        await main_module.on_command_error(ctx, error)
        ctx.send.assert_called_once()
        self.assertIn("member", ctx.send.call_args.args[0])

    async def test_missing_permissions(self):
        ctx = self._make_ctx()
        await main_module.on_command_error(ctx, commands.MissingPermissions(["administrator"]))
        ctx.send.assert_called_once()

    async def test_unexpected_error_goes_to_errors_thread(self):
        ctx = self._make_ctx()
        ctx.guild = MagicMock()

        with patch("main.send_to_log", new_callable=AsyncMock) as mock_log:
            with patch("main.logger"):
                await main_module.on_command_error(ctx, commands.CommandError("boom"))

        ctx.send.assert_not_called()
        mock_log.assert_awaited_once()


class TestBotConfiguration(unittest.TestCase):
    def test_bot_prefix_from_config(self):
        self.assertEqual(main_module.bot.command_prefix, config.CMD_PREFIX)

    def test_intents_message_content(self):
        self.assertTrue(main_module.bot.intents.message_content)

    def test_intents_members(self):
        self.assertTrue(main_module.bot.intents.members)


if __name__ == "__main__":
    unittest.main()
