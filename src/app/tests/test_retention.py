"""Issue #6: фоновая ретенция — заявки и транскрипты старше срока хранения."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import config
from database import migrate_schema, tickets_db
from tickets import retention
from tickets.retention import purge_expired_once, start_retention_loop


def days_ago_iso(days):
    return (datetime.now() - timedelta(days=days)).isoformat()


class RetentionDBTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self._old_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        migrate_schema()

    def tearDown(self):
        config.DB_PATH = self._old_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass


def make_bot(guild_id=7):
    guild = MagicMock()
    guild.id = guild_id
    guild.get_channel = MagicMock(return_value=None)
    bot = MagicMock()
    bot.get_guild = MagicMock(side_effect=lambda gid: guild if gid == guild_id else None)
    return bot, guild


class TestPurgeExpiredOnce(RetentionDBTestCase, unittest.IsolatedAsyncioTestCase):
    def _save_closed(self, channel_id, closed_days, guild_id=7):
        tickets_db.save_ticket(
            channel_id,
            42,
            "vasya",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            days_ago_iso(closed_days + 1),
            guild_id=guild_id,
        )
        tickets_db.update_ticket_status(channel_id, "accepted", 9, "ок")
        tickets_db.get_ticket(channel_id)
        # закрываем с нужной датой вручную: update_ticket_status ставит «сейчас»
        from database.db import get_db

        conn = get_db()
        try:
            conn.execute(
                "UPDATE tickets SET closed_at = ? WHERE channel_id = ?",
                (days_ago_iso(closed_days), channel_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def test_old_closed_ticket_purged_with_logs(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        tickets_db.add_log_message_id(100, 900, 500)
        bot, _ = make_bot()

        with patch("tickets.retention.delete_log_messages", new_callable=AsyncMock) as mock_logs:
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                purged = await purge_expired_once(bot)

        self.assertEqual(purged, 1)
        self.assertIsNone(tickets_db.get_ticket(100))
        mock_logs.assert_awaited_once()
        self.assertEqual(mock_logs.call_args.args[1], [(900, 500)])

    async def test_recent_closed_ticket_kept(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS - 10)
        bot, _ = make_bot()

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_abandoned_open_ticket_channel_deleted_and_row_purged(self):
        tickets_db.save_ticket(
            100,
            42,
            "vasya",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            days_ago_iso(config.TICKET_RETENTION_DAYS + 30),
            guild_id=7,
        )
        channel = MagicMock()
        channel.delete = AsyncMock()
        bot, guild = make_bot()
        guild.get_channel = MagicMock(return_value=channel)

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(bot)

        self.assertEqual(purged, 1)
        channel.delete.assert_awaited_once()
        self.assertIsNone(tickets_db.get_ticket(100))

    async def test_orphan_open_ticket_without_channel_purged(self):
        tickets_db.save_ticket(
            100,
            42,
            "vasya",
            "RP ЗАЯВКА",
            "rp",
            "{}",
            days_ago_iso(config.TICKET_RETENTION_DAYS + 30),
            guild_id=7,
        )
        bot, guild = make_bot()  # guild.get_channel вернёт None — канала давно нет

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(bot)

        self.assertEqual(purged, 1)
        self.assertIsNone(tickets_db.get_ticket(100))

    async def test_fresh_open_ticket_kept(self):
        tickets_db.save_ticket(
            100, 42, "vasya", "RP ЗАЯВКА", "rp", "{}", days_ago_iso(3), guild_id=7
        )
        bot, _ = make_bot()

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
            purged = await purge_expired_once(bot)

        self.assertEqual(purged, 0)
        self.assertIsNotNone(tickets_db.get_ticket(100))

    async def test_aggregated_stats_not_deleted(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        bot, _ = make_bot()

        with patch("tickets.retention.delete_log_messages", new_callable=AsyncMock):
            with patch("tickets.retention.send_to_log", new_callable=AsyncMock):
                await purge_expired_once(bot)

        stats = tickets_db.get_stats(7)
        # записи тикетов удалены ретенцией — сводные счётчики по тикетам
        # отражают окно хранения (документировано в docs/privacy.md)
        self.assertEqual(stats["accepted"], 0)
        # дневная агрегация без персональных данных остаётся
        self.assertTrue(stats["weekly"])
        self.assertEqual(stats["weekly"][0]["accepted"], 1)

    async def test_audit_message_sent_after_purge(self):
        self._save_closed(100, closed_days=config.TICKET_RETENTION_DAYS + 10)
        bot, _ = make_bot()

        with patch("tickets.retention.send_to_log", new_callable=AsyncMock) as mock_log:
            await purge_expired_once(bot)

        mock_log.assert_awaited_once()
        embed = mock_log.call_args.kwargs["embed"]
        self.assertEqual(embed.title, config.RETENTION_AUDIT_TITLE)


class TestRetentionLoop(unittest.TestCase):
    def tearDown(self):
        retention._started = False

    def test_loop_starts_once(self):
        retention._started = False
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()

        with patch("tickets.retention.tasks.loop") as mock_loop:
            decorator = MagicMock()
            mock_loop.return_value = decorator
            start_retention_loop(bot)
            start_retention_loop(bot)  # повторный запуск игнорируется

        mock_loop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
