import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tickets.close_ticket import CloseButton
from tickets.decision import ACCEPT, DecisionReasonModal
from tickets.transcript import build_transcript_file


def _staff_permissions(granted=True):
    return SimpleNamespace(administrator=False, manage_guild=False, manage_messages=granted)


class TestCloseButton(unittest.IsolatedAsyncioTestCase):
    def _make_interaction(self, staff=True):
        interaction = MagicMock()
        interaction.user.mention = "<@9>"
        interaction.user.guild_permissions = _staff_permissions(staff)
        interaction.user.roles = []
        interaction.response.send_message = AsyncMock()
        interaction.channel.id = 123
        interaction.channel.name = "rp-user"
        interaction.channel.delete = AsyncMock()
        interaction.guild.id = 555
        interaction.guild.get_member = MagicMock(return_value=None)
        return interaction

    async def test_staff_closes_ticket(self):
        interaction = self._make_interaction()

        with patch("tickets.close_ticket.get_ticket") as mock_get:
            with patch("tickets.close_ticket.update_ticket_status") as mock_update:
                with patch("tickets.close_ticket.send_to_log", new_callable=AsyncMock) as mock_log:
                    with patch("tickets.close_ticket.add_log_message_id") as mock_track:
                        mock_get.return_value = {"user_id": 7, "topic": "RP ЗАЯВКА"}
                        await CloseButton().callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        mock_update.assert_called_once_with(123, "closed", closed_by=interaction.user.id)
        interaction.channel.delete.assert_awaited_once()
        mock_log.assert_awaited_once()
        mock_track.assert_called_once()

    async def test_regular_member_cannot_close(self):
        interaction = self._make_interaction(staff=False)

        with patch("tickets.close_ticket.update_ticket_status") as mock_update:
            await CloseButton().callback(interaction)

        mock_update.assert_not_called()
        interaction.channel.delete.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_applicant_gets_dm(self):
        interaction = self._make_interaction()
        applicant = MagicMock()
        applicant.send = AsyncMock()
        interaction.guild.get_member = MagicMock(return_value=applicant)

        with patch("tickets.close_ticket.get_ticket") as mock_get:
            with patch("tickets.close_ticket.update_ticket_status"):
                with patch("tickets.close_ticket.send_to_log", new_callable=AsyncMock):
                    with patch("tickets.close_ticket.add_log_message_id"):
                        mock_get.return_value = {"user_id": 7, "topic": "RP ЗАЯВКА"}
                        await CloseButton().callback(interaction)

        applicant.send.assert_awaited_once()


class TestDecisionDoubleClick(unittest.IsolatedAsyncioTestCase):
    async def test_already_decided_ticket_aborts(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, ACCEPT)
        modal.reason = MagicMock()
        modal.reason.value = "Причина"

        interaction = MagicMock()
        interaction.guild.get_member = MagicMock(return_value=None)
        interaction.user.mention = "<@999>"
        interaction.response.send_message = AsyncMock()

        with patch("tickets.decision.update_ticket_status", return_value=False):
            with patch("tickets.decision.get_ticket") as mock_get:
                await modal.on_submit(interaction)

        # тикет уже обработан — второй модератор получает отказ, канал не трогаем
        interaction.response.send_message.assert_called_once()
        mock_get.assert_not_called()
        channel.delete.assert_not_called()


class TestTranscript(unittest.IsolatedAsyncioTestCase):
    async def test_collects_messages(self):
        channel = MagicMock()
        channel.id = 5

        msg = MagicMock()
        msg.created_at = datetime(2024, 1, 1, 10, 0)
        msg.author = "user1"
        msg.clean_content = "привет"

        async def fake_history(**kwargs):
            yield msg

        channel.history = fake_history

        files = await build_transcript_file(channel)
        self.assertIsNotNone(files)
        self.assertEqual(files[0].filename, "ticket-5.txt")
        content = files[0].fp.read().decode("utf-8")
        self.assertIn("user1", content)
        self.assertIn("привет", content)

    async def test_broken_history_returns_none(self):
        channel = MagicMock()
        channel.id = 5
        channel.history = MagicMock(side_effect=Exception("no perms"))

        self.assertIsNone(await build_transcript_file(channel))

    async def test_empty_history_returns_none(self):
        channel = MagicMock()
        channel.id = 5

        async def fake_history(**kwargs):
            return
            yield

        channel.history = fake_history

        self.assertIsNone(await build_transcript_file(channel))


if __name__ == "__main__":
    unittest.main()
