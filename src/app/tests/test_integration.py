"""
Интеграционные тесты end-to-end для Regent FamQ Bot.
Проверяют полный цикл от команды до ответа бота.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from afk.commands import AfkCog
from tickets.commands import TicketsCog, TicketTypeView


class TestIntegrationTicketsFlow(unittest.IsolatedAsyncioTestCase):
    """Интеграция: !regent → Embed → кнопка RP → модалка → создание тикета"""

    async def test_full_regent_flow(self):
        bot = MagicMock()
        cog = TicketsCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()

        # Шаг 1: Команда !regent отправляет Embed с кнопками
        await cog.regent_apply.callback(cog, ctx)
        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        view = call_args.kwargs.get("view")
        self.assertIsInstance(embed, discord.Embed)
        self.assertIsInstance(view, TicketTypeView)
        self.assertEqual(embed.title, config.REGENT_EMBED_TITLE)

    async def test_regent_button_to_modal(self):
        view = TicketTypeView()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        # Шаг 2: Нажатие кнопки RP открывает модалку
        await view.rp_callback(interaction)
        interaction.response.send_modal.assert_called_once()
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.title, config.TICKET_RP_TITLE)

    async def test_stats_command_integration(self):
        bot = MagicMock()
        cog = TicketsCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with patch("tickets.commands.get_stats") as mock_stats:
            mock_stats.return_value = {
                "total": 5,
                "accepted": 3,
                "denied": 1,
                "open": 1,
                "weekly": [],
            }
            await cog.show_stats.callback(cog, ctx)

        ctx.send.assert_called_once()
        embed = ctx.send.call_args.kwargs.get("embed")
        self.assertIsInstance(embed, discord.Embed)
        self.assertEqual(embed.title, "Статистика заявок")

    async def test_history_command_integration(self):
        bot = MagicMock()
        cog = TicketsCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()

        mock_tickets = [
            {"topic": "RP", "user_id": 111, "created_at": "2024-01-01", "status": "accepted"},
            {"topic": "CAPT", "user_id": 222, "created_at": "2024-01-02", "status": "denied"},
        ]

        with patch("tickets.commands.get_all_tickets") as mock_get:
            mock_get.return_value = mock_tickets
            await cog.show_history.callback(cog, ctx, 10)

        ctx.send.assert_called_once()
        embed = ctx.send.call_args.kwargs.get("embed")
        self.assertIsInstance(embed, discord.Embed)


class TestIntegrationAfkFlow(unittest.IsolatedAsyncioTestCase):
    """Интеграция: !afk → меню → взять AFK → модалка → установка AFK"""

    async def test_afk_command_sends_menu(self):
        bot = MagicMock()
        cog = AfkCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()
        ctx.author.mention = "<@123>"

        await cog.afk_command.callback(cog, ctx)
        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed")
        self.assertIsInstance(embed, discord.Embed)

    async def test_afk_list_command_integration(self):
        bot = MagicMock()
        cog = AfkCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        with patch("afk.models.get_all_afk") as mock_get:
            mock_get.return_value = []
            await cog.afk_list_command.callback(cog, ctx)

        ctx.send.assert_called_once()

    async def test_afk_check_command_found(self):
        bot = MagicMock()
        cog = AfkCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        member = MagicMock()
        member.id = 456
        member.mention = "<@456>"

        with patch("afk.commands.get_afk_user") as mock_get:
            mock_get.return_value = {
                "afk_since": "2024-01-01T10:00:00",
                "afk_reason": "test",
                "estimated_return": None,
            }
            await cog.afk_check_command.callback(cog, ctx, member)

        ctx.send.assert_called_once()

    async def test_afk_stats_command_integration(self):
        bot = MagicMock()
        cog = AfkCog(bot)
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 123

        member = MagicMock()
        member.id = 456
        member.display_name = "TestUser"

        with patch("afk.commands.get_user_stats") as mock_get:
            mock_get.return_value = {
                "total_afk_count": 5,
                "total_afk_seconds": 3600,
                "longest_afk_seconds": 1800,
            }
            await cog.afk_stats_command.callback(cog, ctx, member)

        ctx.send.assert_called_once()


class TestIntegrationBotLifecycle(unittest.IsolatedAsyncioTestCase):
    """Интеграция: запуск бота → setup_hook → загрузка когов и views"""

    async def test_setup_hook_loads_extensions(self):
        import main as main_module

        with patch("main.init_db") as mock_init, patch("main.init_afk_db") as mock_afk:
            with patch.object(
                main_module.bot, "load_extension", new_callable=AsyncMock
            ) as mock_load:
                with patch.object(main_module.bot, "add_view"):
                    await main_module.bot.setup_hook()

        mock_init.assert_called_once()
        mock_afk.assert_called_once()
        mock_load.assert_any_await("tickets")
        mock_load.assert_any_await("afk")


if __name__ == "__main__":
    unittest.main()
