import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from tickets.commands import TicketsCog, TicketTypeView


class TestTicketTypeView(unittest.TestCase):
    def test_has_two_buttons(self):
        view = TicketTypeView()
        self.assertEqual(len(view.children), 2)

    def test_rp_button(self):
        view = TicketTypeView()
        rp_btn = view.children[0]
        self.assertEqual(rp_btn.label, config.TICKET_RP_TITLE)
        self.assertEqual(rp_btn.style, discord.ButtonStyle.success)
        self.assertEqual(rp_btn.custom_id, "rp")

    def test_capt_button(self):
        view = TicketTypeView()
        capt_btn = view.children[1]
        self.assertEqual(capt_btn.label, config.TICKET_CAPT_TITLE)
        self.assertEqual(capt_btn.style, discord.ButtonStyle.primary)
        self.assertEqual(capt_btn.custom_id, "capt")


class TestTicketsCog(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.cog = TicketsCog(self.bot)

    def test_cog_name(self):
        self.assertEqual(self.cog.qualified_name, "TicketsCog")

    def test_famqcore_command_exists(self):
        self.assertTrue(hasattr(self.cog, "famqcore_apply"))

    def test_stats_command_exists(self):
        self.assertTrue(hasattr(self.cog, "show_stats"))

    def test_history_command_exists(self):
        self.assertTrue(hasattr(self.cog, "show_history"))

    def test_delete_user_data_command_exists(self):
        self.assertTrue(hasattr(self.cog, "delete_user_data"))


class TestFamqCoreCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = TicketsCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_famqcore_sends_embed(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        self.loop.run_until_complete(self.cog.famqcore_apply.callback(self.cog, ctx))

        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        embed = call_args.kwargs.get("embed") or call_args.args[0]
        self.assertIsInstance(embed, discord.Embed)
        self.assertEqual(embed.title, config.FAMQCORE_EMBED_TITLE)


class TestStatsCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = TicketsCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_stats_sends_embed(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with patch("tickets.commands.get_stats") as mock_stats:
            mock_stats.return_value = {
                "total": 10,
                "accepted": 5,
                "denied": 3,
                "open": 2,
                "weekly": [],
            }
            self.loop.run_until_complete(self.cog.show_stats.callback(self.cog, ctx))

        ctx.send.assert_called_once()


class TestDeleteUserDataCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = TicketsCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_delete_user_data_asks_confirmation_first(self):
        from tickets.commands import DeleteUserDataConfirmView

        ctx = MagicMock()
        ctx.guild.id = 456
        ctx.author.id = 777
        ctx.send = AsyncMock()
        member = MagicMock()
        member.id = 123
        member.mention = "<@123>"

        with patch("tickets.commands.erase_user_data", new_callable=AsyncMock) as mock_erase:
            self.loop.run_until_complete(self.cog.delete_user_data.callback(self.cog, ctx, member))

        # без подтверждения ничего не удаляется
        mock_erase.assert_not_called()
        ctx.send.assert_called_once()
        view = ctx.send.call_args.kwargs["view"]
        self.assertIsInstance(view, DeleteUserDataConfirmView)
        self.assertEqual(view.admin_id, 777)
        self.assertIs(view.member, member)
        # подтверждение не пингует субъекта данных
        allowed = ctx.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual(allowed.users, [])
        self.assertIn("Удалить персональные данные", ctx.send.call_args.args[0])


class TestHistoryCommand(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.cog = TicketsCog(self.bot)

    def tearDown(self):
        self.loop.close()

    def test_history_empty(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with patch("tickets.commands.get_all_tickets") as mock_tickets:
            mock_tickets.return_value = []
            self.loop.run_until_complete(self.cog.show_history.callback(self.cog, ctx, 10))

        ctx.send.assert_called_once()
        self.assertIn("Нет заявок", ctx.send.call_args.args[0])

    def test_history_with_data(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()

        mock_ticket = {
            "topic": "RP ЗАЯВКА",
            "user_id": 123,
            "created_at": "2024-01-01T00:00:00",
            "status": "accepted",
        }

        with patch("tickets.commands.get_all_tickets") as mock_tickets:
            mock_tickets.return_value = [mock_ticket]
            self.loop.run_until_complete(self.cog.show_history.callback(self.cog, ctx, 10))

        ctx.send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
