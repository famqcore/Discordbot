import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from tickets.call_voice import VoiceCallButton, VoiceSelectView
from tickets.close_ticket import CloseButton
from tickets.commands import TicketTypeView
from tickets.create_ticket import TicketModal, create_ticket, sanitize_channel_name
from tickets.decision import ACCEPT, DENY, AcceptButton, DecisionReasonModal, DenyButton
from tickets.views import FullTicketView


class TestTicketModal(unittest.TestCase):
    def test_rp_modal_has_correct_fields(self):
        modal = TicketModal(config.TICKET_RP_TITLE, "rp", config.RP_FIELDS)
        self.assertEqual(modal.title, config.TICKET_RP_TITLE)
        self.assertEqual(len(modal.children), 5)
        self.assertEqual(modal.ticket_type, "rp")

    def test_capt_modal_has_correct_fields(self):
        modal = TicketModal(config.TICKET_CAPT_TITLE, "capt", config.CAPT_FIELDS)
        self.assertEqual(modal.title, config.TICKET_CAPT_TITLE)
        self.assertEqual(len(modal.children), 5)

    def test_modal_input_values(self):
        modal = TicketModal("Test", "rp", config.RP_FIELDS)
        first = modal.children[0]
        self.assertEqual(first.label, config.RP_FIELDS[0][0])
        self.assertEqual(first.placeholder, config.RP_FIELDS[0][1])
        self.assertTrue(first.required)


class TestCreateTicket(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_create_ticket_makes_channel(self):
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.create_text_channel = AsyncMock()
        interaction.guild.create_category = AsyncMock(return_value=MagicMock())
        interaction.guild.default_role = MagicMock()
        interaction.guild.me = MagicMock()
        interaction.guild.roles = []

        interaction.user = MagicMock()
        interaction.user.name = "Tester"
        interaction.user.id = 123
        interaction.user.mention = "<@123>"
        interaction.user.add_roles = AsyncMock()
        interaction.user.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))

        interaction.response = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.mention = "<#456>"
        mock_channel.name = "rp-tester"
        mock_channel.id = 456
        mock_channel.send = AsyncMock()
        interaction.guild.create_text_channel.return_value = mock_channel

        inputs = {
            "Никнейм": MagicMock(value="TestNick"),
        }

        with patch("tickets.create_ticket.save_ticket") as mock_save:
            with patch("tickets.create_ticket.FullTicketView", return_value=MagicMock()):
                self.loop.run_until_complete(create_ticket(interaction, "RP ЗАЯВКА", "rp", inputs))

        interaction.response.send_message.assert_called_once()
        interaction.guild.create_text_channel.assert_called_once()
        mock_save.assert_called_once()


class TestButtons(unittest.TestCase):
    def test_accept_button(self):
        btn = AcceptButton()
        self.assertEqual(btn.label, "Принять")
        self.assertEqual(btn.style, discord.ButtonStyle.success)

    def test_deny_button(self):
        btn = DenyButton()
        self.assertEqual(btn.label, "Отказать")
        self.assertEqual(btn.style, discord.ButtonStyle.danger)

    def test_voice_call_button(self):
        btn = VoiceCallButton()
        self.assertEqual(btn.label, "Вызвать на обзвон")
        self.assertEqual(btn.style, discord.ButtonStyle.primary)

    def test_close_button(self):
        btn = CloseButton()
        self.assertEqual(btn.label, "Закрыть тикет")
        self.assertEqual(btn.style, discord.ButtonStyle.danger)


class TestViews(unittest.TestCase):
    def test_full_ticket_view_has_all_buttons(self):
        view = FullTicketView()
        labels = [child.label for child in view.children]
        self.assertIn("Принять", labels)
        self.assertIn("Отказать", labels)
        self.assertIn("Вызвать на обзвон", labels)
        self.assertIn("Закрыть тикет", labels)

    def test_voice_select_view_has_buttons(self):
        channel = MagicMock()
        view = VoiceSelectView(channel)
        self.assertEqual(len(view.children), len(config.VOICE_CHANNELS))
        for i, btn in enumerate(view.children):
            self.assertEqual(btn.label, config.VOICE_CHANNELS[i])


class TestModals(unittest.TestCase):
    def test_accept_reason_modal(self):
        modal = DecisionReasonModal(MagicMock(), ACCEPT)
        self.assertEqual(modal.title, "Принятие заявки")
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.children[0].label, "Причина принятия")

    def test_deny_reason_modal(self):
        modal = DecisionReasonModal(MagicMock(), DENY)
        self.assertEqual(modal.title, "Отклонение заявки")
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.children[0].label, "Причина отказа")


class TestPersistentButtons(unittest.TestCase):
    # у кнопок должны быть custom_id, иначе после перезапуска они перестанут работать
    def test_ticket_type_view_buttons_have_custom_ids(self):
        view = TicketTypeView()
        for btn in view.children:
            self.assertTrue(btn.custom_id)

    def test_full_ticket_view_buttons_have_custom_ids(self):
        view = FullTicketView()
        self.assertEqual(len(view.children), 4)
        for btn in view.children:
            self.assertTrue(btn.custom_id)

    def test_afk_menu_view_buttons_have_custom_ids(self):
        from afk.views import AfkMenuView

        view = AfkMenuView()
        for btn in view.children:
            self.assertTrue(btn.custom_id)


class TestSanitizeChannelName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(sanitize_channel_name("player_one"), "player_one")

    def test_uppercase_and_spaces(self):
        self.assertEqual(sanitize_channel_name("Player One"), "player-one")

    def test_strips_special_chars(self):
        self.assertEqual(sanitize_channel_name("Pl@yer! #$"), "plyer")

    def test_cyrillic_kept(self):
        self.assertEqual(sanitize_channel_name("Игрок Один"), "игрок-один")

    def test_empty_becomes_default(self):
        self.assertEqual(sanitize_channel_name("!!!"), "user")

    def test_length_capped(self):
        self.assertEqual(len(sanitize_channel_name("a" * 200)), 90)


if __name__ == "__main__":
    unittest.main()
