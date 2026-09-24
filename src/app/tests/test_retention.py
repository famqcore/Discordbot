"""Ретенция заявок: удаление данных старше срока хранения."""

import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

import config
from database import tickets_db
from database.db import run
from database.schema import STATUS_ACCEPTED, STATUS_CLOSED, STATUS_PROCESSING
from tests.support import (
    FakeChannel,
    FakeGuild,
    http_exception,
    make_forbidden,
    use_temp_database,
)
from tickets.retention import (
    TicketRetentionCog,
    purge_expired_once,
    setup_retention_loop,
)
from utils import clock


def days_ago_iso(days: int) -> str:
    return clock.to_db(clock.shift(clock.utcnow(), days=-days))


class PurgeExpiredTestCase(unittest.IsolatedAsyncioTestCase):
    """Один проход ретенции."""

    def setUp(self):
        use_temp_database(self)
        self.guild = FakeGuild(guild_id=7)
        self.bot = self._make_bot(self.guild)

    @staticmethod
    def _make_bot(guild):
        class Bot:
            def get_guild(self, guild_id):
                return guild if guild_id == guild.id else None

        return Bot()

    def _save_closed(self, channel_id: int, closed_days: int, guild_id: int = 7) -> None:
        tickets_db.save_ticket(
            channel_id,
            42,
            "vasya",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            created_at=days_ago_iso(closed_days + 1),
            guild_id=guild_id,
        )
        tickets_db.update_ticket_status(channel_id, STATUS_ACCEPTED, 9, "ок", guild_id=guild_id)
        # update_ticket_status ставит «сейчас» — сдвигаем дату закрытия в прошлое
        closed_at = days_ago_iso(closed_days)
        run(
            lambda conn: conn.execute(
                "UPDATE tickets SET closed_at = ? WHERE channel_id = ?",
                (closed_at, channel_id),
            ),
            write=True,
        )

    def _save_open(self, channel_id: int, created_days: int, guild_id: int = 7) -> None:
        tickets_db.save_ticket(
            channel_id,
            42,
            "vasya",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            created_at=days_ago_iso(created_days),
            guild_id=guild_id,
        )

    async def test_old_closed_ticket_purged_with_logs(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        tickets_db.add_log_message_id(100, 900, 500)

        with patch("tickets.retention.delete_log_messages", new_callable=AsyncMock) as mock_logs:
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 1)
        self.assertIsNone(tickets_db.get_ticket(100))
        mock_logs.assert_awaited_once()
        self.assertEqual(mock_logs.await_args.args[1], [(900, 500)])

    async def test_recent_closed_ticket_kept(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS - 10)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_abandoned_open_ticket_channel_deleted_and_row_purged(self):
        self._save_open(100, created_days=config.TICKET_RETENTION_DAYS + 30)
        channel = FakeChannel(channel_id=100, guild=self.guild)
        self.guild.add_channel(channel)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 1)
        channel.delete.assert_awaited_once()
        self.assertIsNone(tickets_db.get_ticket(100))

    async def test_orphan_open_ticket_without_channel_purged(self):
        self._save_open(100, created_days=config.TICKET_RETENTION_DAYS + 30)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 1)
        self.assertIsNone(tickets_db.get_ticket(100))

    async def test_processing_ticket_is_not_purged_during_terminal_action(self):
        self._save_open(100, created_days=config.TICKET_RETENTION_DAYS + 30)
        self.assertTrue(tickets_db.begin_transition(100, STATUS_CLOSED, self.guild.id))

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_PROCESSING)

    async def test_fresh_open_ticket_kept(self):
        self._save_open(100, created_days=3)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_aggregated_stats_not_deleted(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)

        with patch("tickets.retention.delete_log_messages", new_callable=AsyncMock):
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                await purge_expired_once(self.bot)

        stats = tickets_db.get_stats(7)
        # персональные записи удалены, обезличенная дневная агрегация остаётся
        self.assertEqual(stats["accepted"], 0)
        self.assertTrue(stats["weekly"])
        self.assertEqual(stats["weekly"][0]["accepted"], 1)

    async def test_audit_message_sent_after_purge(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)

        with patch("tickets.retention.delete_log_messages", new_callable=AsyncMock):
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock) as mock_log:
                await purge_expired_once(self.bot)

        mock_log.assert_awaited_once()
        embed = mock_log.await_args.kwargs["embed"]
        self.assertEqual(embed.title, config.RETENTION_AUDIT_TITLE)

    async def test_no_audit_message_when_nothing_purged(self):
        self._save_open(100, created_days=1)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock) as mock_log:
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        mock_log.assert_not_awaited()

    async def test_channel_delete_forbidden_keeps_row(self):
        """Нет прав на удаление канала — запись остаётся до следующего прохода."""
        self._save_open(100, created_days=config.TICKET_RETENTION_DAYS + 30)
        channel = FakeChannel(channel_id=100, guild=self.guild)
        channel.delete = AsyncMock(side_effect=make_forbidden())
        self.guild.add_channel(channel)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_log_deletion_failure_keeps_row(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        tickets_db.add_log_message_id(100, 900, 500)

        with patch(
            "tickets.retention.delete_log_messages",
            new_callable=AsyncMock,
            side_effect=http_exception(),
        ):
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_one_failure_does_not_stop_others(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        self._save_closed(101, closed_days=config.TICKET_RETENTION_DAYS + 11)
        tickets_db.add_log_message_id(100, 900, 500)
        tickets_db.add_log_message_id(101, 901, 501)

        calls = {"n": 0}

        async def flaky(_guild, _refs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_exception()

        with patch("tickets.retention.delete_log_messages", side_effect=flaky):
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 1)

    async def test_unknown_guild_row_still_purged(self):
        """Бота выгнали с сервера: запись всё равно должна уйти по сроку."""
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10, guild_id=999)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(self.bot)

        self.assertEqual(purged, 1)
        self.assertIsNone(tickets_db.get_ticket(100))


class RetentionCogLifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    """Цикл принадлежит Cog и отменяется вместе с ним."""

    async def asyncSetUp(self):
        self.bot = self._make_bot()

    @staticmethod
    async def _settle() -> None:
        import asyncio

        for _ in range(3):
            await asyncio.sleep(0)

    def _make_bot(self) -> commands.Bot:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.wait_until_ready = AsyncMock()
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_setup_starts_single_task(self):
        cog = await setup_retention_loop(self.bot)

        self.assertIsInstance(cog, TicketRetentionCog)
        self.assertTrue(cog.retention_loop.is_running())
        self.assertEqual(cog.retention_loop.seconds, config.RETENTION_CHECK_SECONDS)

    async def test_setup_is_idempotent(self):
        first = await setup_retention_loop(self.bot)
        second = await setup_retention_loop(self.bot)

        self.assertIs(first, second)

    async def test_cog_unload_cancels_task(self):
        cog = await setup_retention_loop(self.bot)

        await self.bot.remove_cog("TicketRetentionCog")
        await self._settle()

        self.assertFalse(cog.retention_loop.is_running())

    async def test_two_instances_are_independent(self):
        other = self._make_bot()
        first = await setup_retention_loop(self.bot)
        second = await setup_retention_loop(other)

        await self.bot.remove_cog("TicketRetentionCog")
        await self._settle()

        self.assertFalse(first.retention_loop.is_running())
        self.assertTrue(second.retention_loop.is_running())


if __name__ == "__main__":
    unittest.main()
