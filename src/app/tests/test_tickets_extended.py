import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from tickets.call_voice import VoiceCallButton, VoiceSelectView
from tickets.close_ticket import CloseButton
from tickets.create_ticket import create_ticket
from tickets.decision import ACCEPT, DENY, AcceptButton, DecisionReasonModal, DenyButton


class TestAcceptButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_sends_modal(self):
        btn = AcceptButton()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()
        interaction.channel = MagicMock()

        await btn.callback(interaction)
        interaction.response.send_modal.assert_called_once()


class TestAcceptReasonModalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_submit_success(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, ACCEPT)
        modal.reason = MagicMock()
        modal.reason.value = "Хорошая заявка"

        guild = MagicMock()
        guild.channels = []
        guild.create_text_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))

        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = MagicMock()
        interaction.user.mention = "<@999>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        mock_ticket = {"user_id": 456}
        mock_member = MagicMock()
        mock_member.mention = "<@456>"
        guild.get_member = MagicMock(return_value=mock_member)

        with patch("tickets.decision.get_ticket") as mock_get:
            with patch("tickets.decision.update_ticket_status") as mock_update:
                with patch("tickets.decision.discord.utils.get", return_value=None):
                    with patch("tickets.decision.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.decision.add_log_message_id"):
                            mock_get.return_value = mock_ticket
                            await modal.on_submit(interaction)

        mock_update.assert_called_once()
        channel.send.assert_called_once()
        interaction.response.send_message.assert_called_once()
        channel.delete.assert_called_once()

    async def test_submit_no_ticket(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, ACCEPT)
        modal.reason = MagicMock()
        modal.reason.value = "Причина"

        guild = MagicMock()
        guild.channels = []
        guild.create_text_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))
        guild.get_member = MagicMock(return_value=None)

        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = MagicMock()
        interaction.user.mention = "<@999>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("tickets.decision.get_ticket") as mock_get:
            with patch("tickets.decision.update_ticket_status") as mock_update:
                with patch("tickets.decision.discord.utils.get", return_value=None):
                    with patch("tickets.decision.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.decision.add_log_message_id"):
                            mock_get.return_value = None
                            await modal.on_submit(interaction)

        mock_update.assert_called_once()
        channel.send.assert_called_once()


class TestDenyButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_sends_modal(self):
        btn = DenyButton()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()
        interaction.channel = MagicMock()

        await btn.callback(interaction)
        interaction.response.send_modal.assert_called_once()


class TestDenyReasonModalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_submit_success(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, DENY)
        modal.reason = MagicMock()
        modal.reason.value = "Не подходит"

        guild = MagicMock()
        guild.channels = []
        guild.create_text_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))

        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = MagicMock()
        interaction.user.mention = "<@999>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        mock_ticket = {"user_id": 456}
        mock_member = MagicMock()
        mock_member.mention = "<@456>"
        guild.get_member = MagicMock(return_value=mock_member)

        with patch("tickets.decision.get_ticket") as mock_get:
            with patch("tickets.decision.update_ticket_status") as mock_update:
                with patch("tickets.decision.discord.utils.get", return_value=None):
                    with patch("tickets.decision.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.decision.add_log_message_id"):
                            mock_get.return_value = mock_ticket
                            await modal.on_submit(interaction)

        mock_update.assert_called_once()
        channel.send.assert_called_once()
        interaction.response.send_message.assert_called_once()
        channel.delete.assert_called_once()

    async def test_submit_no_ticket(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, DENY)
        modal.reason = MagicMock()
        modal.reason.value = "Причина"

        guild = MagicMock()
        guild.channels = []
        guild.create_text_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))
        guild.get_member = MagicMock(return_value=None)

        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = MagicMock()
        interaction.user.mention = "<@999>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("tickets.decision.get_ticket") as mock_get:
            with patch("tickets.decision.update_ticket_status") as mock_update:
                with patch("tickets.decision.discord.utils.get", return_value=None):
                    with patch("tickets.decision.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.decision.add_log_message_id"):
                            mock_get.return_value = None
                            await modal.on_submit(interaction)

        mock_update.assert_called_once()
        channel.send.assert_called_once()


class TestVoiceCallButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_sends_view(self):
        btn = VoiceCallButton()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.channel = MagicMock()

        await btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        self.assertIn("view", call_args.kwargs)


class TestVoiceSelectViewButtons(unittest.IsolatedAsyncioTestCase):
    async def test_voice_button_found(self):
        ticket_channel = MagicMock()
        ticket_channel.id = 123
        ticket_channel.send = AsyncMock()

        view = VoiceSelectView(ticket_channel)
        btn = view.children[0]

        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.mention = "<@ recruiter>"
        interaction.guild = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        voice_ch = MagicMock()
        voice_ch.mention = "<#voice1>"
        interaction.guild.voice_channels = [voice_ch]
        interaction.guild.get_member = MagicMock(return_value=MagicMock(mention="<@456>"))

        with patch("tickets.call_voice.get_ticket") as mock_get:
            mock_get.return_value = {"user_id": 456}
            with patch("tickets.call_voice.discord.utils.get", return_value=voice_ch):
                with patch("config.ALLOW_NAME_FALLBACK", True):
                    await btn.callback(interaction)

        ticket_channel.send.assert_called()
        interaction.response.send_message.assert_called_once()

    async def test_voice_button_not_found(self):
        ticket_channel = MagicMock()
        ticket_channel.id = 123
        ticket_channel.send = AsyncMock()

        view = VoiceSelectView(ticket_channel)
        btn = view.children[0]

        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.mention = "<@ recruiter>"
        interaction.guild = MagicMock()
        interaction.guild.voice_channels = []
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("tickets.call_voice.get_ticket") as mock_get:
            mock_get.return_value = {"user_id": 456}
            with patch("tickets.call_voice.discord.utils.get", return_value=None):
                await btn.callback(interaction)

        ticket_channel.send.assert_called_once()
        interaction.response.send_message.assert_called_once()


class TestCloseButton(unittest.IsolatedAsyncioTestCase):
    async def test_callback_deletes_channel(self):
        btn = CloseButton()
        interaction = MagicMock()
        interaction.user.mention = "<@9>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.channel = MagicMock()
        interaction.channel.id = 123
        interaction.channel.delete = AsyncMock()
        interaction.guild.get_member = MagicMock(return_value=None)

        with patch("tickets.close_ticket.get_ticket", return_value=None):
            with patch("tickets.close_ticket.update_ticket_status") as mock_update:
                with patch("tickets.close_ticket.send_to_log", new_callable=AsyncMock):
                    with patch("tickets.close_ticket.add_log_message_id"):
                        await btn.callback(interaction)

        interaction.response.send_message.assert_called_once()
        interaction.channel.delete.assert_called_once()
        mock_update.assert_called_once()


class TestCreateTicketErrors(unittest.IsolatedAsyncioTestCase):
    async def test_create_ticket_forbidden_role(self):
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.roles = []
        interaction.guild.me = MagicMock()
        interaction.guild.create_text_channel = AsyncMock()
        interaction.guild.create_category = AsyncMock(return_value=MagicMock())
        interaction.guild.default_role = MagicMock()

        interaction.user = MagicMock()
        interaction.user.name = "Tester"
        interaction.user.id = 123
        interaction.user.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "No perms")
        )
        interaction.user.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))

        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.mention = "<#456>"
        mock_channel.name = "rp-tester"
        mock_channel.id = 456
        mock_channel.send = AsyncMock()
        interaction.guild.create_text_channel.return_value = mock_channel

        inputs = {"Никнейм": MagicMock(value="TestNick")}

        with patch("tickets.create_ticket.save_ticket"):
            with patch("tickets.create_ticket.FullTicketView", return_value=MagicMock()):
                with patch("tickets.create_ticket.discord.utils.get", return_value=None):
                    with patch("tickets.create_ticket.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.create_ticket.add_log_message_id"):
                            await create_ticket(interaction, "RP ЗАЯВКА", "rp", inputs)

        interaction.edit_original_response.assert_called_once()
        call_args = interaction.edit_original_response.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        self.assertIn("Заявка создана", text)

    async def test_create_ticket_dm_forbidden(self):
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.roles = []
        interaction.guild.me = MagicMock()
        interaction.guild.create_text_channel = AsyncMock()
        interaction.guild.create_category = AsyncMock(return_value=MagicMock())
        interaction.guild.default_role = MagicMock()

        interaction.user = MagicMock()
        interaction.user.name = "Tester"
        interaction.user.id = 123
        interaction.user.add_roles = AsyncMock()
        interaction.user.create_dm = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "DM closed")
        )

        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.mention = "<#456>"
        mock_channel.name = "rp-tester"
        mock_channel.id = 456
        mock_channel.send = AsyncMock()
        interaction.guild.create_text_channel.return_value = mock_channel

        inputs = {"Никнейм": MagicMock(value="TestNick")}

        with patch("tickets.create_ticket.save_ticket"):
            with patch("tickets.create_ticket.FullTicketView", return_value=MagicMock()):
                with patch("tickets.create_ticket.discord.utils.get", return_value=None):
                    with patch("tickets.create_ticket.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.create_ticket.add_log_message_id"):
                            await create_ticket(interaction, "RP ЗАЯВКА", "rp", inputs)

        interaction.edit_original_response.assert_called_once()

    async def test_create_ticket_exception(self):
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.roles = []
        interaction.guild.me = MagicMock()
        interaction.guild.create_text_channel = AsyncMock(side_effect=Exception("DB error"))
        interaction.guild.create_category = AsyncMock(return_value=MagicMock())
        interaction.guild.default_role = MagicMock()

        interaction.user = MagicMock()
        interaction.user.name = "Tester"
        interaction.user.id = 123
        interaction.user.add_roles = AsyncMock()
        interaction.user.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))

        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        inputs = {"Никнейм": MagicMock(value="TestNick")}

        with patch("tickets.create_ticket.discord.utils.get", return_value=None):
            with patch("tickets.create_ticket.send_to_log", new_callable=AsyncMock):
                with patch("tickets.create_ticket.add_log_message_id"):
                    await create_ticket(interaction, "RP ЗАЯВКА", "rp", inputs)

        interaction.edit_original_response.assert_called_once()
        call_args = interaction.edit_original_response.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        self.assertIn(config.ERROR_TICKET_CREATE, text)


if __name__ == "__main__":
    unittest.main()
