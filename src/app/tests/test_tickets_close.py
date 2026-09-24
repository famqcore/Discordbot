"""Терминальные действия по заявке: закрытие и решение.

Проверяется идемпотентность (двойной клик, гонка), порядок шагов и
поведение при ошибках Discord: канал не должен исчезать раньше, чем
переписка сохранена, а заявка — раньше, чем зафиксирован статус.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from database import tickets_db
from database.schema import STATUS_ACCEPTED, STATUS_CLOSED, STATUS_OPEN, STATUS_PROCESSING
from tests.support import (
    FakeChannel,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeMessage,
    http_exception,
    make_forbidden,
    make_not_found,
    use_temp_database,
)
from tickets.close_ticket import CloseButton
from tickets.decision import ACCEPT, DENY, DecisionReasonModal
from tickets.transcript import build_transcript, build_transcript_file
from tickets.workflow import complete_terminal_action
from utils import clock


def staff_permissions(granted=True):
    return SimpleNamespace(administrator=False, manage_guild=False, manage_messages=granted)


class TerminalActionTestCase(unittest.IsolatedAsyncioTestCase):
    """Общая обвязка: настоящая БД, фейковые Discord-объекты."""

    CHANNEL_ID = 123
    GUILD_ID = 555
    APPLICANT_ID = 7

    def setUp(self):
        use_temp_database(self)
        self.guild = FakeGuild(guild_id=self.GUILD_ID)
        self.applicant = FakeMember(user_id=self.APPLICANT_ID, name="applicant")
        self.guild.add_member(self.applicant)
        self.moderator = FakeMember(user_id=9, name="mod")
        self.moderator.guild_permissions = staff_permissions()
        self.channel = FakeChannel(
            channel_id=self.CHANNEL_ID,
            name="rp-user",
            guild=self.guild,
            messages=[FakeMessage(content="привет", author=self.applicant)],
        )
        self.guild.add_channel(self.channel)
        tickets_db.save_ticket(
            self.CHANNEL_ID,
            self.APPLICANT_ID,
            "applicant",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            guild_id=self.GUILD_ID,
        )

    def interaction(self, staff: bool = True) -> FakeInteraction:
        user = self.moderator if staff else FakeMember(user_id=42, name="member")
        user.guild_permissions = staff_permissions(staff)
        user.roles = []
        return FakeInteraction(user=user, guild=self.guild, channel=self.channel)

    def ticket(self):
        return tickets_db.get_ticket(self.CHANNEL_ID)


class CloseButtonTestCase(TerminalActionTestCase):
    async def test_staff_closes_ticket(self):
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)
        self.assertEqual(self.ticket()["closed_by"], self.moderator.id)
        self.channel.delete.assert_awaited_once()
        mock_log.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()

    async def test_regular_member_cannot_close(self):
        interaction = self.interaction(staff=False)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.channel.delete.assert_not_awaited()
        self.assertIn(config.TICKET_NO_PERMISSION, interaction.sent_texts())

    async def test_applicant_gets_dm_before_channel_deleted(self):
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await CloseButton().callback(interaction)

        self.applicant.send.assert_awaited_once()
        self.channel.delete.assert_awaited_once()

    async def test_double_click_closes_once(self):
        """Два клика подряд дают одно закрытие, один лог и одно удаление."""
        first = self.interaction()
        second = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await CloseButton().callback(first)
            await CloseButton().callback(second)

        self.assertEqual(mock_log.await_count, 1)
        self.channel.delete.assert_awaited_once()
        self.assertIn(config.TICKET_ALREADY_DECIDED, second.sent_texts())

    async def test_concurrent_clicks_single_winner(self):
        import asyncio

        interactions = [self.interaction() for _ in range(5)]

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await asyncio.gather(*(CloseButton().callback(inter) for inter in interactions))

        self.assertEqual(mock_log.await_count, 1)
        self.channel.delete.assert_awaited_once()
        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)

    async def test_channel_without_ticket_row_rejected(self):
        other = FakeChannel(channel_id=999, guild=self.guild)
        interaction = FakeInteraction(user=self.moderator, guild=self.guild, channel=other)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await CloseButton().callback(interaction)

        mock_log.assert_not_awaited()
        other.delete.assert_not_awaited()

    async def test_ticket_of_other_guild_rejected(self):
        """Кросс-гильдийная защита: чужой тикет не закрывается."""
        alien = FakeGuild(guild_id=777)
        alien.add_channel(self.channel)
        interaction = FakeInteraction(user=self.moderator, guild=alien, channel=self.channel)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await CloseButton().callback(interaction)

        mock_log.assert_not_awaited()
        self.assertEqual(self.ticket()["status"], STATUS_OPEN)

    async def test_ticket_lookup_failure_is_reported_by_boundary(self):
        interaction = self.interaction()

        with patch(
            "tickets.close_ticket.ticket_for_channel",
            new_callable=AsyncMock,
            side_effect=RuntimeError("database unavailable"),
        ):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.assertTrue(any("Код ошибки" in text for text in interaction.sent_texts()))

    async def test_unexpected_failure_releases_close_claim(self):
        interaction = self.interaction()

        with patch(
            "tickets.close_ticket.complete_terminal_action",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected failure"),
        ):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.assertTrue(any("Код ошибки" in text for text in interaction.sent_texts()))

    async def test_transcript_failure_keeps_channel_and_reopens(self):
        """Переписку сохранить не удалось — канал остаётся, статус возвращается."""
        self.channel.history = MagicMock(side_effect=make_forbidden())
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await CloseButton().callback(interaction)

        mock_log.assert_not_awaited()
        self.applicant.send.assert_not_awaited()
        self.channel.delete.assert_not_awaited()
        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        interaction.followup.send.assert_awaited_once()

    async def test_retryable_log_error_releases_ticket(self):
        """5xx при отправке лога: заявка снова открыта, модератор повторит."""
        interaction = self.interaction()

        with patch(
            "tickets.workflow.send_to_log",
            new_callable=AsyncMock,
            side_effect=http_exception(503),
        ):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.channel.delete.assert_not_awaited()

    async def test_permanent_log_error_still_finalizes(self):
        """Ошибка 4xx лога не должна блокировать закрытие заявки."""
        interaction = self.interaction()

        with patch(
            "tickets.workflow.send_to_log",
            new_callable=AsyncMock,
            side_effect=http_exception(400),
        ):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)
        self.channel.delete.assert_awaited_once()

    async def test_channel_delete_failure_keeps_status_closed(self):
        """Канал не удалился — статус уже терминальный, уборку доделает reconcile."""
        self.channel.delete = AsyncMock(side_effect=make_forbidden())
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)

    async def test_channel_already_deleted_is_not_an_error(self):
        self.channel.delete = AsyncMock(side_effect=make_not_found())
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await CloseButton().callback(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)

    async def test_log_message_reference_saved(self):
        interaction = self.interaction()
        log_message = FakeMessage(message_id=500)
        log_message.channel = SimpleNamespace(id=900)

        with patch(
            "tickets.workflow.send_to_log", new_callable=AsyncMock, return_value=log_message
        ):
            await CloseButton().callback(interaction)

        refs = tickets_db.parse_log_message_refs(self.ticket()["log_message_ids"])
        self.assertEqual(refs, [(900, 500)])


class DecisionTestCase(TerminalActionTestCase):
    def modal(self, decision=ACCEPT, reason="Причина"):
        modal = DecisionReasonModal(self.channel, decision)
        modal.reason = MagicMock(value=reason)
        return modal

    async def test_accept_marks_ticket_accepted(self):
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self.modal().on_submit(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_ACCEPTED)
        self.channel.delete.assert_awaited_once()
        self.applicant.send.assert_awaited_once()

    async def test_deny_marks_ticket_denied(self):
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self.modal(DENY).on_submit(interaction)

        self.assertEqual(self.ticket()["status"], "denied")

    async def test_already_decided_ticket_aborts(self):
        interaction = self.interaction()
        tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID)
        tickets_db.finalize_transition(self.CHANNEL_ID, STATUS_CLOSED, 1, None)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await self.modal().on_submit(interaction)

        mock_log.assert_not_awaited()
        self.channel.delete.assert_not_awaited()
        self.assertIn(config.TICKET_ALREADY_DECIDED, interaction.sent_texts())

    async def test_ticket_lookup_failure_is_reported_by_boundary(self):
        interaction = self.interaction()

        with patch(
            "tickets.decision.ticket_for_channel",
            new_callable=AsyncMock,
            side_effect=RuntimeError("database unavailable"),
        ):
            await self.modal().on_submit(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.assertTrue(any("Код ошибки" in text for text in interaction.sent_texts()))

    async def test_unexpected_failure_releases_decision_claim(self):
        interaction = self.interaction()

        with patch(
            "tickets.decision.complete_terminal_action",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected failure"),
        ):
            await self.modal().on_submit(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.assertTrue(any("Код ошибки" in text for text in interaction.sent_texts()))

    async def test_accept_then_deny_race_single_decision(self):
        """Одновременные «Принять» и «Отказать» обрабатываются один раз."""
        import asyncio

        accept = self.interaction()
        deny = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock) as mock_log:
            await asyncio.gather(
                self.modal(ACCEPT).on_submit(accept),
                self.modal(DENY).on_submit(deny),
            )

        self.assertEqual(mock_log.await_count, 1)
        self.assertIn(self.ticket()["status"], {STATUS_ACCEPTED, "denied"})
        self.channel.delete.assert_awaited_once()

    async def test_stats_counted_once_per_ticket(self):
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self.modal().on_submit(interaction)
            await self.modal().on_submit(self.interaction())

        stats = tickets_db.get_stats(self.GUILD_ID)
        self.assertEqual(stats["accepted"], 1)

    async def test_dm_failure_does_not_block_decision(self):
        self.applicant.send = AsyncMock(side_effect=make_forbidden())
        interaction = self.interaction()

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            await self.modal().on_submit(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_ACCEPTED)

    async def test_retryable_log_error_does_not_notify_about_reverted_decision(self):
        interaction = self.interaction()

        with patch(
            "tickets.workflow.send_to_log",
            new_callable=AsyncMock,
            side_effect=http_exception(503),
        ):
            await self.modal().on_submit(interaction)

        self.assertEqual(self.ticket()["status"], STATUS_OPEN)
        self.applicant.send.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.channel.delete.assert_not_awaited()


class WorkflowStateTestCase(TerminalActionTestCase):
    """Промежуточное состояние processing и его снятие."""

    async def test_claim_moves_to_processing(self):
        self.assertTrue(tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID))

        row = self.ticket()
        self.assertEqual(row["status"], STATUS_PROCESSING)
        self.assertEqual(row["pending_status"], STATUS_CLOSED)
        self.assertIsNotNone(row["processing_at"])

    async def test_release_returns_to_open(self):
        tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID)

        self.assertTrue(tickets_db.release_transition(self.CHANNEL_ID))
        row = self.ticket()
        self.assertEqual(row["status"], STATUS_OPEN)
        self.assertIsNone(row["pending_status"])
        self.assertIsNone(row["processing_at"])

    async def test_finalize_requires_matching_claim(self):
        tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID)

        self.assertFalse(tickets_db.finalize_transition(self.CHANNEL_ID, STATUS_ACCEPTED, 1, None))
        self.assertTrue(tickets_db.finalize_transition(self.CHANNEL_ID, STATUS_CLOSED, 1, None))

    async def test_processing_ticket_blocks_new_claim(self):
        tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID)

        self.assertFalse(
            tickets_db.begin_transition(self.CHANNEL_ID, STATUS_ACCEPTED, self.GUILD_ID)
        )

    async def test_terminal_action_without_deletion_keeps_channel(self):
        tickets_db.begin_transition(self.CHANNEL_ID, STATUS_CLOSED, self.GUILD_ID)

        with patch("tickets.workflow.send_to_log", new_callable=AsyncMock):
            outcome = await complete_terminal_action(
                guild=self.guild,
                channel=self.channel,
                status=STATUS_CLOSED,
                actor=self.moderator,
                reason=None,
                embed=discord.Embed(title="x"),
                delete_channel=False,
            )

        self.assertTrue(outcome.ok)
        self.channel.delete.assert_not_awaited()
        self.assertEqual(self.ticket()["status"], STATUS_CLOSED)


class TranscriptTestCase(unittest.IsolatedAsyncioTestCase):
    """Состав и полнота снимка переписки."""

    def _channel(self, messages):
        guild = FakeGuild(guild_id=1, name="Guild")
        return FakeChannel(channel_id=5, name="ticket", guild=guild, messages=messages)

    async def test_collects_messages(self):
        author = FakeMember(user_id=11, name="user1")
        channel = self._channel(
            [FakeMessage(content="привет", author=author, created_at=clock.utcnow())]
        )

        files = await build_transcript_file(channel)

        self.assertIsNotNone(files)
        self.assertEqual(files[0].filename, "ticket-5.txt")
        content = files[0].fp.read().decode("utf-8")
        self.assertIn("user1", content)
        self.assertIn("привет", content)

    async def test_header_documents_contract(self):
        channel = self._channel([FakeMessage(content="текст")])

        files = await build_transcript_file(channel)
        content = files[0].fp.read().decode("utf-8")

        self.assertIn("Сервер:", content)
        self.assertIn("Канал:", content)
        self.assertIn("Сообщений в снимке: 1", content)
        self.assertIn("НЕ входят", content)

    async def test_embeds_and_attachments_recorded(self):
        embed = discord.Embed(title="Заявка", description="описание")
        embed.add_field(name="Никнейм", value="Tester")
        attachment = SimpleNamespace(filename="screen.png", content_type="image/png", size=2048)
        channel = self._channel(
            [FakeMessage(content="см. вложение", embeds=[embed], attachments=[attachment])]
        )

        files = await build_transcript_file(channel)
        content = files[0].fp.read().decode("utf-8")

        self.assertIn("Заявка", content)
        self.assertIn("[embed:Никнейм] Tester", content)
        self.assertIn("screen.png", content)
        self.assertIn("image/png", content)
        self.assertIn("2048", content)

    async def test_more_than_limit_messages_truncated(self):
        messages = [FakeMessage(message_id=i, content=f"msg{i}") for i in range(250)]
        channel = self._channel(messages)

        transcript = await build_transcript(channel, limit=200)

        self.assertTrue(transcript.truncated)
        self.assertEqual(transcript.message_count, 200)
        self.assertFalse(transcript.complete)
        self.assertIn("обрезана", transcript.status_note())

    async def test_large_history_is_size_limited(self):
        # немного очень больших сообщений быстрее, чем тысячи мелких
        messages = [FakeMessage(message_id=i, content="x" * 200_000) for i in range(40)]
        channel = self._channel(messages)

        transcript = await build_transcript(channel)

        self.assertTrue(transcript.size_limited)
        self.assertFalse(transcript.complete)
        self.assertLessEqual(transcript.files[0].fp.getbuffer().nbytes, 7 * 1024 * 1024 + 100)

    async def test_forbidden_history_reports_failure(self):
        channel = self._channel([])
        channel.history = MagicMock(side_effect=make_forbidden())

        transcript = await build_transcript(channel)

        self.assertTrue(transcript.failed)
        self.assertIn("прав", transcript.error)
        self.assertIn("⚠️", transcript.status_note())

    async def test_api_error_reports_failure(self):
        channel = self._channel([])
        channel.history = MagicMock(side_effect=http_exception())

        transcript = await build_transcript(channel)

        self.assertTrue(transcript.failed)
        self.assertIsNone(await build_transcript_file(channel))

    async def test_empty_history_returns_none(self):
        channel = self._channel([])

        transcript = await build_transcript(channel)

        self.assertEqual(transcript.message_count, 0)
        self.assertIsNone(await build_transcript_file(channel))
        self.assertFalse(transcript.failed)

    async def test_rejects_message_limit_outside_supported_range(self):
        with self.assertRaises(ValueError):
            await build_transcript(self._channel([]), limit=0)


if __name__ == "__main__":
    unittest.main()
