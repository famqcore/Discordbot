"""Кнопки и модалки заявок: права, ветвления и обработка ошибок.

Дополняет test_tickets_close.py, где проверяется идемпотентность
терминальных действий, и test_tickets.py с юнит-проверками форм.
"""

import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from database.schema import STATUS_ACCEPTED, STATUS_OPEN
from database.tickets_db import get_open_ticket_for_user, get_ticket, save_ticket
from tests.support import (
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    http_exception,
    make_forbidden,
    use_temp_database,
)
from tickets.call_voice import VoiceCallButton, VoiceSelectView
from tickets.close_ticket import CloseButton
from tickets.create_ticket import TicketSubmission, create_ticket
from tickets.decision import ACCEPT, DENY, AcceptButton, DecisionReasonModal, DenyButton


def staff_permissions(granted=True):
    return SimpleNamespace(administrator=False, manage_guild=False, manage_messages=granted)


def make_staff(user_id=9, name="mod"):
    member = FakeMember(user_id=user_id, name=name)
    member.guild_permissions = staff_permissions(True)
    member.roles = []
    return member


class DecisionButtonTestCase(unittest.IsolatedAsyncioTestCase):
    """Кнопки «Принять»/«Отказать» открывают модалку только персоналу."""

    async def test_staff_gets_modal(self):
        interaction = FakeInteraction(user=make_staff())
        interaction.channel = FakeChannel()

        await AcceptButton().callback(interaction)

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, DecisionReasonModal)
        self.assertIs(modal.decision, ACCEPT)

    async def test_deny_button_uses_deny_decision(self):
        interaction = FakeInteraction(user=make_staff())
        interaction.channel = FakeChannel()

        await DenyButton().callback(interaction)

        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIs(modal.decision, DENY)

    async def test_regular_member_denied(self):
        member = FakeMember(user_id=42)
        member.guild_permissions = staff_permissions(False)
        member.roles = []
        interaction = FakeInteraction(user=member)
        interaction.channel = FakeChannel()

        await AcceptButton().callback(interaction)

        interaction.response.send_modal.assert_not_awaited()
        self.assertIn(config.TICKET_NO_PERMISSION, interaction.sent_texts())


class DecisionSubmitTestCase(unittest.IsolatedAsyncioTestCase):
    """Отправка модалки решения на реальной записи в БД."""

    CHANNEL_ID = 123
    GUILD_ID = 555

    def setUp(self):
        use_temp_database(self)
        self.guild = FakeGuild(guild_id=self.GUILD_ID)
        self.applicant = FakeMember(user_id=456, name="applicant")
        self.guild.add_member(self.applicant)
        self.channel = FakeChannel(channel_id=self.CHANNEL_ID, guild=self.guild)
        self.guild.add_channel(self.channel)
        self.moderator = make_staff()

    def _save(self):
        save_ticket(self.CHANNEL_ID, 456, "applicant", "RP", "rp", "{}", guild_id=self.GUILD_ID)

    def _modal(self, decision=ACCEPT, reason="Хорошая заявка"):
        modal = DecisionReasonModal(self.channel, decision)
        modal.reason = MagicMock(value=reason)
        return modal

    def _interaction(self):
        return FakeInteraction(user=self.moderator, guild=self.guild, channel=self.channel)

    async def test_submit_success(self):
        self._save()
        interaction = self._interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await self._modal().on_submit(interaction)

        self.assertEqual(get_ticket(self.CHANNEL_ID)["status"], STATUS_ACCEPTED)
        self.channel.send.assert_awaited_once()
        self.channel.delete.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()
        mock_log.assert_awaited_once()

    async def test_deny_submit_success(self):
        self._save()
        interaction = self._interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self._modal(DENY, "Не подходит").on_submit(interaction)

        self.assertEqual(get_ticket(self.CHANNEL_ID)["status"], "denied")
        self.applicant.send.assert_awaited_once()

    async def test_submit_no_ticket(self):
        """Канал без записи — решение невозможно, канал не трогаем."""
        interaction = self._interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await self._modal().on_submit(interaction)

        mock_log.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.channel.delete.assert_not_awaited()
        self.assertIn(config.TICKET_ALREADY_DECIDED, interaction.sent_texts())

    async def test_reason_stored_in_ticket(self):
        self._save()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self._modal(reason="Годная анкета").on_submit(self._interaction())

        self.assertEqual(get_ticket(self.CHANNEL_ID)["reason"], "Годная анкета")

    async def test_applicant_left_guild_is_handled(self):
        self._save()
        guild = FakeGuild(guild_id=self.GUILD_ID)
        guild.add_channel(self.channel)
        interaction = FakeInteraction(user=self.moderator, guild=guild, channel=self.channel)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self._modal().on_submit(interaction)

        self.assertEqual(get_ticket(self.CHANNEL_ID)["status"], STATUS_ACCEPTED)


class VoiceCallTestCase(unittest.IsolatedAsyncioTestCase):
    """Вызов на обзвон."""

    async def test_staff_gets_channel_picker(self):
        interaction = FakeInteraction(user=make_staff())
        interaction.channel = FakeChannel()

        await VoiceCallButton().callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        self.assertIn("view", interaction.response.send_message.await_args.kwargs)

    async def test_regular_member_denied(self):
        member = FakeMember(user_id=42)
        member.guild_permissions = staff_permissions(False)
        member.roles = []
        interaction = FakeInteraction(user=member)
        interaction.channel = FakeChannel()

        await VoiceCallButton().callback(interaction)

        self.assertIn(config.TICKET_NO_PERMISSION, interaction.sent_texts())

    async def test_voice_button_invites_applicant(self):
        use_temp_database(self)
        guild = FakeGuild(guild_id=1)
        applicant = FakeMember(user_id=456, name="applicant")
        guild.add_member(applicant)
        ticket_channel = FakeChannel(channel_id=123, guild=guild)
        save_ticket(123, 456, "applicant", "RP", "rp", "{}", guild_id=1)

        voice = MagicMock()
        voice.id = 77
        voice.mention = "<#77>"

        view = VoiceSelectView(ticket_channel)
        interaction = FakeInteraction(user=make_staff(), guild=guild)

        with patch("tickets.call_voice.get_voice_channel", return_value=voice):
            with patch("tickets.call_voice.send_to_log", new_callable=AsyncMock) as mock_log:
                await view.children[0].callback(interaction)

        self.assertEqual(ticket_channel.send.await_count, 2)
        interaction.response.send_message.assert_awaited_once()
        mock_log.assert_awaited_once()
        # приглашение адресовано только заявителю
        allowed = ticket_channel.send.await_args.kwargs["allowed_mentions"]
        self.assertEqual(allowed.users, [applicant])
        self.assertFalse(allowed.everyone)

    async def test_voice_channel_not_found(self):
        use_temp_database(self)
        guild = FakeGuild(guild_id=1)
        ticket_channel = FakeChannel(channel_id=123, guild=guild)
        view = VoiceSelectView(ticket_channel)
        interaction = FakeInteraction(user=make_staff(), guild=guild)

        with patch("tickets.call_voice.get_voice_channel", return_value=None):
            await view.children[0].callback(interaction)

        ticket_channel.send.assert_awaited_once()
        self.assertIn("не найден", ticket_channel.send.await_args.args[0])
        interaction.response.send_message.assert_awaited_once()

    async def test_send_failure_is_reported_once(self):
        """Ошибка Discord уходит в error boundary, а не в трейс пользователю."""
        use_temp_database(self)
        guild = FakeGuild(guild_id=1)
        ticket_channel = FakeChannel(channel_id=123, guild=guild)
        ticket_channel.send = AsyncMock(side_effect=http_exception())
        voice = MagicMock(id=77, mention="<#77>")
        view = VoiceSelectView(ticket_channel)
        interaction = FakeInteraction(user=make_staff(), guild=guild)

        with patch("tickets.call_voice.get_voice_channel", return_value=voice):
            await view.children[0].callback(interaction)

        self.assertTrue(interaction.sent_texts())


class CloseButtonPermissionTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_channel_without_ticket_is_not_deleted(self):
        use_temp_database(self)
        guild = FakeGuild(guild_id=1)
        channel = FakeChannel(channel_id=123, guild=guild)
        interaction = FakeInteraction(user=make_staff(), guild=guild, channel=channel)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await CloseButton().callback(interaction)

        channel.delete.assert_not_awaited()
        self.assertIn(config.TICKET_ALREADY_DECIDED, interaction.sent_texts())


class CreateTicketErrorsTestCase(unittest.IsolatedAsyncioTestCase):
    """Поведение при сбое на каждом шаге создания."""

    GUILD_ID = 321
    USER_ID = 123

    def setUp(self):
        use_temp_database(self)
        self.guild = FakeGuild(guild_id=self.GUILD_ID)
        self.member = FakeMember(user_id=self.USER_ID, name="Tester")
        self.guild.add_member(self.member)
        self.channel = FakeChannel(channel_id=456, name="rp-tester", guild=self.guild)
        self.guild.create_text_channel = AsyncMock(return_value=self.channel)
        self.interaction = FakeInteraction(user=self.member, guild=self.guild)
        self.interaction.edit_original_response = AsyncMock()
        self.inputs = {"Никнейм": MagicMock(value="TestNick")}

    def _reply_text(self) -> str:
        call = self.interaction.edit_original_response.await_args
        if call is None:
            return ""
        return call.args[0] if call.args else call.kwargs.get("content", "")

    async def _create(self, **patches):
        defaults = {
            "get_category": patch("tickets.create_ticket.get_category", return_value=MagicMock()),
            "get_role": patch("tickets.create_ticket.get_role", return_value=None),
            "send_to_log": patch(
                "tickets.create_ticket.send_to_log", new_callable=AsyncMock, return_value=None
            ),
            "view": patch("tickets.create_ticket.FullTicketView", return_value=MagicMock()),
        }
        defaults.update(patches)
        started = [ctx.start() for ctx in defaults.values()]
        self.addCleanup(lambda: [ctx.stop() for ctx in defaults.values()])
        del started
        return await create_ticket(
            self.interaction,
            TicketSubmission(
                config.RP_FORM,
                {label: text_input.value for label, text_input in self.inputs.items()},
            ),
        )

    async def test_role_grant_forbidden_does_not_fail_ticket(self):
        """Не выдалась роль — заявка всё равно создана."""
        role = MagicMock(spec=discord.Role)
        role.id = 5
        role.mention = "<@&5>"
        role.__lt__ = lambda self, other: True
        self.member.add_roles = AsyncMock(side_effect=make_forbidden())

        await self._create(get_role=patch("tickets.create_ticket.get_role", return_value=role))

        self.assertIn("Заявка создана", self._reply_text())
        self.assertIsNotNone(get_open_ticket_for_user(self.GUILD_ID, self.USER_ID))

    async def test_dm_forbidden_does_not_fail_ticket(self):
        self.member.send = AsyncMock(side_effect=make_forbidden())

        await self._create()

        self.assertIn("Заявка создана", self._reply_text())
        self.assertIsNotNone(get_open_ticket_for_user(self.GUILD_ID, self.USER_ID))

    async def test_card_send_failure_does_not_fail_ticket(self):
        self.channel.send = AsyncMock(side_effect=http_exception())

        await self._create()

        self.assertIn("Заявка создана", self._reply_text())
        self.assertIsNotNone(get_open_ticket_for_user(self.GUILD_ID, self.USER_ID))

    async def test_channel_creation_failure_leaves_no_row(self):
        self.guild.create_text_channel = AsyncMock(side_effect=make_forbidden())

        result = await self._create()

        self.assertIsNone(result)
        self.assertIn(config.ERROR_TICKET_CREATE, self._reply_text())
        self.assertIsNone(get_open_ticket_for_user(self.GUILD_ID, self.USER_ID))

    async def test_db_failure_removes_created_channel(self):
        """Если запись не сохранилась, канал не остаётся сиротой."""
        with patch("tickets.create_ticket.async_save_ticket", new_callable=AsyncMock, side_effect=sqlite3.OperationalError("x")):
            result = await self._create()

        self.assertIsNone(result)
        self.channel.delete.assert_awaited_once()
        self.assertIn(config.ERROR_TICKET_CREATE, self._reply_text())
        self.assertIsNone(get_open_ticket_for_user(self.GUILD_ID, self.USER_ID))

    async def test_duplicate_submit_removes_extra_channel(self):
        """Параллельный submit: победитель один, лишний канал удаляется."""
        save_ticket(999, self.USER_ID, "Tester", "RP", "rp", "{}", guild_id=self.GUILD_ID)

        with patch(
            "tickets.create_ticket.async_get_open_ticket_for_user",
            side_effect=[None, {"channel_id": 999}],
        ):
            with patch(
                "tickets.create_ticket.async_save_ticket", side_effect=sqlite3.IntegrityError("unique")
            ):
                result = await self._create()

        self.assertIsNone(result)
        self.channel.delete.assert_awaited_once()
        self.assertIn("999", self._reply_text())

    async def test_existing_ticket_short_circuits(self):
        save_ticket(777, self.USER_ID, "Tester", "RP", "rp", "{}", guild_id=self.GUILD_ID)

        result = await self._create()

        self.assertIsNone(result)
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIn("777", self._reply_text())

    async def test_successful_ticket_is_persisted(self):
        result = await self._create()

        self.assertIs(result, self.channel)
        row = get_open_ticket_for_user(self.GUILD_ID, self.USER_ID)
        self.assertEqual(row["status"], STATUS_OPEN)
        self.assertEqual(json.loads(row["answers"]), {"Никнейм": "TestNick"})


if __name__ == "__main__":
    unittest.main()
