"""Приёмочные тесты по issue #4: пользовательский ввод не создаёт пинги.

Каждый недоверенный текст (причина AFK, причина решения) проверяется на
@everyone / @here / упоминание роли и участника в точке отправки, а служебные
пинги остаются адресными.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from afk.events import setup_afk_events
from afk.views import AfkSetModal
from tickets.decision import ACCEPT, DecisionReasonModal

ATTACK_TEXT = "@everyone @here <@123456789012345678> <@&987654321098765432>"


def assert_no_pings(test_case, content):
    """Текст не содержит работающих конструкций упоминаний."""
    test_case.assertNotIn("@everyone", content)
    test_case.assertNotIn("@here", content)
    test_case.assertNotIn("<@123456789012345678>", content)
    test_case.assertNotIn("<@&987654321098765432>", content)


class TestAfkAutoReplyPingSafety(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = MagicMock()
        self.bot.process_commands = AsyncMock()
        setup_afk_events(self.bot)
        self.on_message = self.bot.event.call_args_list[0][0][0]

    def tearDown(self):
        self.loop.close()

    def _make_message(self):
        message = MagicMock()
        message.author.bot = False
        message.guild = MagicMock()
        message.guild.id = 123
        message.content = "эй, ты тут?"
        message.author.id = 100
        mention = MagicMock()
        mention.id = 200
        mention.mention = "<@200>"
        message.mentions = [mention]
        message.channel = MagicMock()
        message.channel.send = AsyncMock()
        return message, mention

    def _run(self, reason):
        message, mention = self._make_message()
        row = {"afk_since": "2024-01-01T10:00:00", "afk_reason": reason}
        with patch("afk.events.get_afk_user", return_value=row):
            with patch("afk.events.check_and_reply", return_value=True):
                self.loop.run_until_complete(self.on_message(message))
        return message, mention

    def test_everyone_in_reason_cannot_ping(self):
        message, _ = self._run("@everyone")
        content = message.channel.send.call_args.args[0]
        self.assertNotIn("@everyone", content)

    def test_here_in_reason_cannot_ping(self):
        message, _ = self._run("@here собирайтесь")
        content = message.channel.send.call_args.args[0]
        self.assertNotIn("@here", content)

    def test_role_mention_in_reason_cannot_ping(self):
        message, _ = self._run("роль <@&987654321098765432>")
        content = message.channel.send.call_args.args[0]
        self.assertNotIn("<@&987654321098765432>", content)

    def test_user_mention_in_reason_cannot_ping(self):
        message, _ = self._run("<@123456789012345678> иди сюда")
        content = message.channel.send.call_args.args[0]
        self.assertNotIn("<@123456789012345678>", content)

    def test_all_attacks_combined_neutralized(self):
        message, _ = self._run(ATTACK_TEXT)
        assert_no_pings(self, message.channel.send.call_args.args[0])

    def test_allowed_mentions_addressed_to_afk_user_only(self):
        message, mention = self._run(ATTACK_TEXT)
        allowed = message.channel.send.call_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [mention])

    def test_service_mention_of_afk_user_preserved(self):
        message, mention = self._run("Отошёл")
        content = message.channel.send.call_args.args[0]
        # служебный пинг AFK-пользователя — задуманное поведение, он остаётся
        self.assertIn("<@200>", content)


class TestAfkSetModalPingSafety(unittest.IsolatedAsyncioTestCase):
    async def test_reason_escaped_in_confirmation(self):
        member = MagicMock()
        member.id = 456
        member.edit = AsyncMock()
        modal = AfkSetModal(member, 123, MagicMock())
        modal.reason = MagicMock()
        modal.reason.value = ATTACK_TEXT
        modal.duration = MagicMock()
        modal.duration.value = "1 час"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.models.set_afk"):
            with patch("afk.models.add_afk_nickname"):
                with patch("afk.views.send_to_log", new_callable=AsyncMock):
                    await modal.on_submit(interaction)

        content = interaction.response.send_message.call_args.args[0]
        assert_no_pings(self, content)
        allowed = interaction.response.send_message.call_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [])


class TestDecisionReasonPingSafety(unittest.IsolatedAsyncioTestCase):
    async def test_reason_escaped_everywhere_and_mentions_addressed(self):
        channel = MagicMock()
        channel.id = 123
        channel.send = AsyncMock()
        channel.delete = AsyncMock()

        modal = DecisionReasonModal(channel, ACCEPT)
        modal.reason = MagicMock()
        modal.reason.value = ATTACK_TEXT

        applicant = MagicMock()
        applicant.mention = "<@456>"
        applicant.send = AsyncMock()

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=applicant)

        interaction = MagicMock()
        interaction.guild = guild
        interaction.user = MagicMock()
        interaction.user.mention = "<@999>"
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("tickets.decision.update_ticket_status", return_value=True):
            with patch("tickets.decision.get_ticket", return_value={"user_id": 456}):
                with patch("tickets.decision.send_to_log", new_callable=AsyncMock):
                    with patch("tickets.decision.add_log_message_id"):
                        await modal.on_submit(interaction)

        # сообщение в канал тикета
        content = channel.send.call_args.args[0]
        assert_no_pings(self, content)
        allowed = channel.send.call_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [applicant])

        # личное сообщение заявителю
        dm_content = applicant.send.call_args.args[0]
        assert_no_pings(self, dm_content)


class TestCreateTicketServicePing(unittest.IsolatedAsyncioTestCase):
    """Пинг рекрутёров в новом тикете — адресный: только нужные роли и заявитель."""

    async def test_recruiter_ping_is_addressed(self):
        recruiter = MagicMock()
        recruiter.id = 10
        recruiter.mention = "<@&10>"

        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.id = 321
        interaction.guild.create_text_channel = AsyncMock()
        interaction.guild.create_category = AsyncMock(return_value=MagicMock())
        interaction.guild.default_role = MagicMock()
        interaction.guild.me = MagicMock()
        interaction.guild.roles = []

        member = MagicMock()
        member.name = "Tester"
        member.id = 123
        member.mention = "<@123>"
        member.add_roles = AsyncMock()
        member.create_dm = AsyncMock(return_value=MagicMock(send=AsyncMock()))
        interaction.user = member

        interaction.response = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        channel = MagicMock()
        channel.mention = "<#456>"
        channel.name = "rp-tester"
        channel.id = 456
        channel.send = AsyncMock()
        interaction.guild.create_text_channel.return_value = channel

        def fake_get_role(guild, role_id, name=None):
            return recruiter if name == config.ROLE_RECRUITER else None

        with patch("tickets.create_ticket.get_role", side_effect=fake_get_role):
            with patch("tickets.create_ticket.save_ticket"):
                with patch("tickets.create_ticket.get_open_ticket_for_user", return_value=None):
                    with patch("tickets.create_ticket.send_to_log", new_callable=AsyncMock):
                        with patch("tickets.create_ticket.add_log_message_id"):
                            with patch(
                                "tickets.create_ticket.FullTicketView", return_value=MagicMock()
                            ):
                                from tickets.create_ticket import create_ticket

                                await create_ticket(
                                    interaction,
                                    config.TICKET_RP_TITLE,
                                    "rp",
                                    {"Никнейм": MagicMock(value="TestNick")},
                                )

        ping_call = channel.send.call_args_list[1]
        self.assertIn("<@&10>", ping_call.args[0])
        allowed = ping_call.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [recruiter])
        self.assertEqual(allowed.users, [member])


if __name__ == "__main__":
    unittest.main()
