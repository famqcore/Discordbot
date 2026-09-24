"""Reconciliation заявок.

Пара «запись в БД — канал Discord» может разойтись при падении процесса
или отказе API. Проверяется, что фоновый проход приводит состояние к
согласованному и при этом не трогает чужие каналы.
"""

import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

import config
from database import tickets_db
from database.schema import STATUS_CLOSED, STATUS_OPEN, STATUS_PROCESSING
from tests.support import (
    FakeChannel,
    FakeGuild,
    http_exception,
    make_forbidden,
    make_not_found,
    use_temp_database,
)
from tickets.reconcile import (
    CLOSE_REASON_NO_CHANNEL,
    TicketReconcileCog,
    reconcile_once,
    setup_reconcile_loop,
)
from utils import clock


class ReconcileTestCase(unittest.IsolatedAsyncioTestCase):
    GUILD_ID = 7

    def setUp(self):
        use_temp_database(self)
        self.guild = FakeGuild(guild_id=self.GUILD_ID)
        self.bot = self._make_bot(self.guild)

    @staticmethod
    def _make_bot(*guilds):
        class Bot:
            def __init__(self, items):
                self.guilds = list(items)
                self._by_id = {guild.id: guild for guild in items}

            def get_guild(self, guild_id):
                return self._by_id.get(guild_id)

        return Bot(guilds)

    def _ticket(self, channel_id: int, user_id: int = 1, guild_id: int | None = None) -> None:
        tickets_db.save_ticket(
            channel_id,
            user_id,
            "user",
            "RP",
            "rp",
            "{}",
            guild_id=guild_id if guild_id is not None else self.GUILD_ID,
        )

    def _make_stale_processing(self, channel_id: int, target=STATUS_CLOSED) -> None:
        """Заявка захвачена под терминальное действие и «зависла»."""
        tickets_db.begin_transition(channel_id, target, self.GUILD_ID)
        stale = clock.to_db(
            clock.shift(clock.utcnow(), seconds=-config.TICKET_PROCESSING_TIMEOUT_SECONDS - 60)
        )
        from database.db import run

        run(
            lambda conn: conn.execute(
                "UPDATE tickets SET processing_at = ? WHERE channel_id = ?", (stale, channel_id)
            ),
            write=True,
        )

    # -- активная заявка без канала ----------------------------------------

    async def test_active_ticket_without_channel_is_closed(self):
        self._ticket(100)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 1)
        row = tickets_db.get_ticket(100)
        self.assertEqual(row["status"], STATUS_CLOSED)
        self.assertEqual(row["reason"], CLOSE_REASON_NO_CHANNEL)

    async def test_closing_frees_unique_index_for_new_ticket(self):
        """Главный смысл: пользователь снова может подать заявку."""
        self._ticket(100, user_id=42)

        await reconcile_once(self.bot)

        self.assertIsNone(tickets_db.get_open_ticket_for_user(self.GUILD_ID, 42))
        tickets_db.save_ticket(101, 42, "user", "RP", "rp", "{}", guild_id=self.GUILD_ID)
        self.assertEqual(tickets_db.get_open_ticket_for_user(self.GUILD_ID, 42)["channel_id"], 101)

    async def test_live_channel_keeps_open_ticket(self):
        self._ticket(100)
        self.guild.add_channel(FakeChannel(channel_id=100, guild=self.guild))

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_OPEN)

    async def test_channel_found_only_via_api_is_kept(self):
        """Канала нет в кэше, но он существует — заявку трогать нельзя."""
        self._ticket(100)
        self.guild.fetch_channel = AsyncMock(
            return_value=FakeChannel(channel_id=100, guild=self.guild)
        )

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_OPEN)

    async def test_forbidden_fetch_treated_as_missing(self):
        self._ticket(100)
        self.guild.fetch_channel = AsyncMock(side_effect=make_forbidden())

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 1)

    async def test_api_error_defers_decision(self):
        """5xx: неизвестно, есть канал или нет — на этом проходе не решаем."""
        self._ticket(100)
        self.guild.fetch_channel = AsyncMock(side_effect=http_exception(503))

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_OPEN)

    async def test_unavailable_guild_is_skipped(self):
        self._ticket(100, guild_id=999)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["checked"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_OPEN)

    # -- зависший processing ------------------------------------------------

    async def test_stale_processing_with_live_channel_released(self):
        self._ticket(100)
        self.guild.add_channel(FakeChannel(channel_id=100, guild=self.guild))
        self._make_stale_processing(100)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["released_processing"], 1)
        row = tickets_db.get_ticket(100)
        self.assertEqual(row["status"], STATUS_OPEN)
        self.assertIsNone(row["pending_status"])
        self.assertIsNone(row["processing_at"])

    async def test_released_ticket_can_be_claimed_again(self):
        self._ticket(100)
        self.guild.add_channel(FakeChannel(channel_id=100, guild=self.guild))
        self._make_stale_processing(100)

        await reconcile_once(self.bot)

        self.assertTrue(tickets_db.begin_transition(100, STATUS_CLOSED, self.GUILD_ID))

    async def test_fresh_processing_is_not_released(self):
        """Модератор ещё работает — прерывать его нельзя."""
        self._ticket(100)
        self.guild.add_channel(FakeChannel(channel_id=100, guild=self.guild))
        tickets_db.begin_transition(100, STATUS_CLOSED, self.GUILD_ID)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["released_processing"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_PROCESSING)

    async def test_stale_processing_without_channel_is_closed(self):
        self._ticket(100)
        self._make_stale_processing(100)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 1)
        self.assertEqual(counters["released_processing"], 0)
        self.assertEqual(tickets_db.get_ticket(100)["status"], STATUS_CLOSED)

    # -- границы ответственности -------------------------------------------

    async def test_channel_without_db_row_is_never_deleted(self):
        """Бот не удаляет каналы, которые мог создать не он."""
        orphan = FakeChannel(channel_id=555, guild=self.guild)
        self.guild.add_channel(orphan)

        await reconcile_once(self.bot)

        orphan.delete.assert_not_awaited()

    async def test_terminal_tickets_are_ignored(self):
        self._ticket(100)
        tickets_db.begin_transition(100, STATUS_CLOSED, self.GUILD_ID)
        tickets_db.finalize_transition(100, STATUS_CLOSED, 1, None)

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["checked"], 0)

    async def test_idempotent_second_pass_changes_nothing(self):
        self._ticket(100)

        first = await reconcile_once(self.bot)
        second = await reconcile_once(self.bot)

        self.assertEqual(first["closed_without_channel"], 1)
        self.assertEqual(second["closed_without_channel"], 0)

    async def test_multiple_guilds_processed_independently(self):
        other = FakeGuild(guild_id=8)
        bot = self._make_bot(self.guild, other)
        self._ticket(100, user_id=1, guild_id=self.GUILD_ID)
        self._ticket(200, user_id=2, guild_id=8)
        other.add_channel(FakeChannel(channel_id=200, guild=other))

        counters = await reconcile_once(bot)

        self.assertEqual(counters["checked"], 2)
        self.assertEqual(counters["closed_without_channel"], 1)
        self.assertEqual(tickets_db.get_ticket(200)["status"], STATUS_OPEN)

    async def test_not_found_channel_closes_ticket(self):
        self._ticket(100)
        self.guild.fetch_channel = AsyncMock(side_effect=make_not_found())

        counters = await reconcile_once(self.bot)

        self.assertEqual(counters["closed_without_channel"], 1)


class ReconcileCogLifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # цикл стартует сразу после wait_until_ready: настоящий проход по БД
        # здесь не нужен, проверяется только жизненный цикл задачи
        patcher = patch(
            "tickets.reconcile.reconcile_once",
            new_callable=AsyncMock,
            return_value={"closed_without_channel": 0, "released_processing": 0, "checked": 0},
        )
        self.mock_reconcile = patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = self._make_bot()

    @staticmethod
    async def _settle():
        import asyncio

        for _ in range(3):
            await asyncio.sleep(0)

    def _make_bot(self) -> commands.Bot:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.wait_until_ready = AsyncMock()
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_setup_starts_loop_with_configured_interval(self):
        cog = await setup_reconcile_loop(self.bot)

        self.assertIsInstance(cog, TicketReconcileCog)
        self.assertTrue(cog.reconcile_loop.is_running())
        self.assertEqual(cog.reconcile_loop.seconds, config.TICKET_RECONCILE_CHECK_SECONDS)

    async def test_setup_is_idempotent(self):
        first = await setup_reconcile_loop(self.bot)
        second = await setup_reconcile_loop(self.bot)

        self.assertIs(first, second)

    async def test_cog_unload_cancels_loop(self):
        cog = await setup_reconcile_loop(self.bot)

        await self.bot.remove_cog("TicketReconcileCog")
        await self._settle()

        self.assertFalse(cog.reconcile_loop.is_running())

    async def test_loop_failure_does_not_stop_the_task(self):
        """Ошибка одного прохода не должна убивать фоновую задачу."""
        cog = await setup_reconcile_loop(self.bot)

        self.mock_reconcile.side_effect = RuntimeError("db down")

        with patch("utils.errors.logger"):
            await cog.reconcile_loop.coro(cog)

        self.assertTrue(cog.reconcile_loop.is_running())


if __name__ == "__main__":
    unittest.main()
