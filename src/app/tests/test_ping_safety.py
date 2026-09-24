"""Приёмочные тесты: пользовательский ввод не создаёт пинги.

Каждый недоверенный текст (причина AFK, причина решения) проверяется на
@everyone / @here / упоминание роли и участника в точке отправки, а служебные
пинги остаются адресными.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from afk.events import AfkEventsCog
from afk.views import AfkSetModal
from database.afk_db import AfkSetResult
from tests.support import FakeChannel, FakeGuild, FakeInteraction, FakeMember
from tickets.decision import ACCEPT, DecisionReasonModal
from tickets.workflow import TerminalOutcome
from utils import clock, ratelimit

ATTACK_TEXT = "@everyone @here <@123456789012345678> <@&987654321098765432>"


def assert_no_pings(test_case, content):
    """Текст не содержит работающих конструкций упоминаний."""
    test_case.assertNotIn("@everyone", content)
    test_case.assertNotIn("@here", content)
    test_case.assertNotIn("<@123456789012345678>", content)
    test_case.assertNotIn("<@&987654321098765432>", content)


class AfkAutoReplyPingSafetyTestCase(unittest.IsolatedAsyncioTestCase):
    """Причина AFK попадает в публичный автоответ — она обязана быть обезврежена."""

    def setUp(self):
        ratelimit.reset()
        self.addCleanup(ratelimit.reset)
        self.cog = AfkEventsCog(MagicMock())

    async def _run(self, reason):
        guild = FakeGuild(guild_id=123)
        target = FakeMember(user_id=200, name="afk-user")
        message = MagicMock(spec=discord.Message)
        message.author = FakeMember(user_id=100, name="author")
        message.guild = guild
        message.channel = FakeChannel(channel_id=55, guild=guild)
        message.content = "эй, ты тут?"
        message.mentions = [target]

        row = {
            "user_id": 200,
            "afk_reason": reason,
            "afk_since": clock.to_db(clock.shift(clock.utcnow(), minutes=-10)),
        }
        with patch("afk.events.async_get_afk_users", new_callable=AsyncMock, return_value={200: row}):
            with patch("afk.events.async_check_and_reply", new_callable=AsyncMock, return_value=True):
                await self.cog.on_message(message)
        return message, target

    async def test_everyone_in_reason_cannot_ping(self):
        message, _ = await self._run("@everyone")

        self.assertNotIn("@everyone", message.channel.send.await_args.args[0])

    async def test_here_in_reason_cannot_ping(self):
        message, _ = await self._run("@here собирайтесь")

        self.assertNotIn("@here", message.channel.send.await_args.args[0])

    async def test_role_mention_in_reason_cannot_ping(self):
        message, _ = await self._run("роль <@&987654321098765432>")

        self.assertNotIn("<@&987654321098765432>", message.channel.send.await_args.args[0])

    async def test_user_mention_in_reason_cannot_ping(self):
        message, _ = await self._run("<@123456789012345678> иди сюда")

        self.assertNotIn("<@123456789012345678>", message.channel.send.await_args.args[0])

    async def test_all_attacks_combined_neutralized(self):
        message, _ = await self._run(ATTACK_TEXT)

        assert_no_pings(self, message.channel.send.await_args.args[0])

    async def test_allowed_mentions_addressed_to_afk_user_only(self):
        message, target = await self._run(ATTACK_TEXT)

        allowed = message.channel.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [target])

    async def test_service_mention_of_afk_user_preserved(self):
        message, _ = await self._run("Отошёл")

        # служебный пинг AFK-пользователя — задуманное поведение, он остаётся
        self.assertIn("<@200>", message.channel.send.await_args.args[0])


class AfkSetModalPingSafetyTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_reason_escaped_in_confirmation(self):
        guild = FakeGuild(guild_id=123)
        member = FakeMember(user_id=456, name="tester")
        guild.add_member(member)

        modal = AfkSetModal(member, 123, guild)
        modal.reason = MagicMock(value=ATTACK_TEXT)
        modal.duration = MagicMock(value="1 час")

        interaction = FakeInteraction(user=member, guild=guild)

        with patch("afk.views.async_set_afk", new_callable=AsyncMock, return_value=AfkSetResult(created=True)):
            with patch("afk.views.async_get_afk_user", new_callable=AsyncMock, return_value=None):
                with patch("afk.views.add_afk_nickname", new_callable=AsyncMock, return_value=True):
                    with patch("afk.views.send_to_log", new_callable=AsyncMock):
                        await modal.on_submit(interaction)

        call = interaction.response.send_message.await_args
        assert_no_pings(self, call.args[0])
        allowed = call.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [])

    async def test_reason_escaped_in_log_embed(self):
        guild = FakeGuild(guild_id=123)
        member = FakeMember(user_id=456)
        modal = AfkSetModal(member, 123, guild)
        modal.reason = MagicMock(value=ATTACK_TEXT)
        modal.duration = MagicMock(value="1 час")
        interaction = FakeInteraction(user=member, guild=guild)

        with patch("afk.views.async_set_afk", new_callable=AsyncMock, return_value=AfkSetResult(created=True)):
            with patch("afk.views.async_get_afk_user", new_callable=AsyncMock, return_value=None):
                with patch("afk.views.add_afk_nickname", new_callable=AsyncMock, return_value=True):
                    with patch("afk.views.send_to_log", new_callable=AsyncMock) as mock_log:
                        await modal.on_submit(interaction)

        embed = mock_log.await_args.kwargs["embed"]
        reason_field = next(f for f in embed.fields if f.name == "Причина")
        assert_no_pings(self, reason_field.value)


class DecisionReasonPingSafetyTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_reason_escaped_everywhere_and_mentions_addressed(self):
        guild = FakeGuild(guild_id=321)
        applicant = FakeMember(user_id=456, name="applicant")
        guild.add_member(applicant)
        moderator = FakeMember(user_id=999, name="mod")
        channel = FakeChannel(channel_id=123, guild=guild)

        modal = DecisionReasonModal(channel, ACCEPT)
        modal.reason = MagicMock(value=ATTACK_TEXT)

        interaction = FakeInteraction(user=moderator, guild=guild, channel=channel)

        async def complete_with_notification(**kwargs):
            await kwargs["after_finalize"]()
            return TerminalOutcome(ok=True)

        with patch(
            "tickets.decision.ticket_for_channel",
            new_callable=AsyncMock,
            return_value={"user_id": 456},
        ):
            with patch("tickets.decision.claim_ticket", new_callable=AsyncMock, return_value=True):
                with patch(
                    "tickets.decision.complete_terminal_action",
                    new_callable=AsyncMock,
                    side_effect=complete_with_notification,
                ) as mock_complete:
                    await modal.on_submit(interaction)

        # сообщение в канал тикета
        content = channel.send.await_args.args[0]
        assert_no_pings(self, content)
        allowed = channel.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [applicant])

        # личное сообщение заявителю
        dm_content = applicant.send.await_args.args[0]
        assert_no_pings(self, dm_content)
        dm_allowed = applicant.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(dm_allowed.everyone)

        # embed в лог-центр
        embed = mock_complete.await_args.kwargs["embed"]
        reason_field = next(f for f in embed.fields if f.name == "Причина")
        assert_no_pings(self, reason_field.value)


class CreateTicketServicePingTestCase(unittest.IsolatedAsyncioTestCase):
    """Пинг рекрутёров в новом тикете — адресный: только нужные роли и заявитель."""

    async def test_recruiter_ping_is_addressed(self):
        guild = FakeGuild(guild_id=321)
        member = FakeMember(user_id=123, name="Tester")
        guild.add_member(member)

        recruiter = MagicMock(spec=discord.Role)
        recruiter.id = 10
        recruiter.mention = "<@&10>"

        channel = FakeChannel(channel_id=456, name="rp-tester", guild=guild)
        guild.create_text_channel = AsyncMock(return_value=channel)
        guild.me.top_role = MagicMock()

        interaction = FakeInteraction(user=member, guild=guild)
        interaction.edit_original_response = AsyncMock()

        def fake_get_role(_guild, _role_id, name=None):
            return recruiter if name == config.ROLE_RECRUITER else None

        with patch("tickets.create_ticket.get_role", side_effect=fake_get_role):
            with patch("tickets.create_ticket.get_category", return_value=MagicMock()):
                with patch("tickets.create_ticket.async_save_ticket"):
                    with patch("tickets.create_ticket.async_get_open_ticket_for_user", new_callable=AsyncMock, return_value=None):
                        with patch(
                            "tickets.create_ticket.send_to_log",
                            new_callable=AsyncMock,
                            return_value=None,
                        ):
                            with patch(
                                "tickets.create_ticket.FullTicketView", return_value=MagicMock()
                            ):
                                from tickets.create_ticket import TicketSubmission, create_ticket

                                await create_ticket(
                                    interaction,
                                    TicketSubmission(config.RP_FORM, {"Никнейм": "TestNick"}),
                                )

        ping_call = channel.send.await_args_list[1]
        self.assertIn("<@&10>", ping_call.args[0])
        allowed = ping_call.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [recruiter])
        self.assertEqual(allowed.users, [member])


if __name__ == "__main__":
    unittest.main()
