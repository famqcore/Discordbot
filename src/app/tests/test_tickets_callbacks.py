import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from tickets.commands import TicketTypeView
from tickets.create_ticket import TicketModal


class TestTicketTypeViewCallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_rp_callback_sends_modal(self):
        view = TicketTypeView()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        await view.rp_callback(interaction)
        interaction.response.send_modal.assert_called_once()
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, TicketModal)
        self.assertEqual(modal.title, config.TICKET_RP_TITLE)
        self.assertEqual(modal.ticket_type, "rp")

    async def test_capt_callback_sends_modal(self):
        view = TicketTypeView()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        await view.capt_callback(interaction)
        interaction.response.send_modal.assert_called_once()
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, TicketModal)
        self.assertEqual(modal.title, config.TICKET_CAPT_TITLE)
        self.assertEqual(modal.ticket_type, "capt")


class TestTicketModalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_on_submit_calls_create_ticket(self):
        modal = TicketModal(config.RP_FORM)
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.response = MagicMock()

        with patch("tickets.create_ticket.create_ticket") as mock_create:
            await modal.on_submit(interaction)
            mock_create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
