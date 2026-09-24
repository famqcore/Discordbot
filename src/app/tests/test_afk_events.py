"""Автоответ на упоминание AFK.

Проверяется как поведение слушателя, так и защита от burst-нагрузки:
дедупликация, лимит проверок, один агрегированный ответ, атомарный
кулдаун и возврат резерва при сбое отправки.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

import config
from afk.events import AfkEventsCog, build_reply, collect_mention_ids, setup_afk_events
from tests.support import FakeChannel, FakeGuild, FakeMember, http_exception, make_forbidden
from utils import clock, ratelimit


def make_message(
    *,
    author=None,
    guild=None,
    channel=None,
    content: str = "привет",
    mentions=(),
):
    guild = guild if guild is not None else FakeGuild(guild_id=123)
    message = MagicMock(spec=discord.Message)
    message.author = author if author is not None else FakeMember(user_id=100, name="author")
    message.guild = guild
    message.channel = channel if channel is not None else FakeChannel(channel_id=55, guild=guild)
    message.content = content
    message.mentions = list(mentions)
    return message


def afk_row(user_id: int, reason: str = "обед") -> dict:
    return {
        "user_id": user_id,
        "guild_id": 123,
        "afk_reason": reason,
        "afk_since": clock.to_db(clock.shift(clock.utcnow(), minutes=-30)),
        "estimated_return": None,
        "original_nick": None,
        "nick_applied": 0,
        "is_afk": 1,
    }


class CollectMentionsTestCase(unittest.TestCase):
    """Дедупликация и ограничение количества проверок."""

    def test_duplicates_collapsed(self):
        target = FakeMember(user_id=200)
        message = make_message(mentions=[target, target, target])

        self.assertEqual(collect_mention_ids(message), [200])

    def test_author_self_mention_ignored(self):
        author = FakeMember(user_id=100)
        message = make_message(author=author, mentions=[author, FakeMember(user_id=200)])

        self.assertEqual(collect_mention_ids(message), [200])

    def test_bots_ignored(self):
        bot_member = FakeMember(user_id=300, bot=True)
        message = make_message(mentions=[bot_member, FakeMember(user_id=200)])

        self.assertEqual(collect_mention_ids(message), [200])

    def test_capped_at_configured_limit(self):
        mentions = [
            FakeMember(user_id=200 + i) for i in range(config.AFK_MAX_MENTIONS_PER_MESSAGE * 3)
        ]
        message = make_message(mentions=mentions)

        self.assertEqual(len(collect_mention_ids(message)), config.AFK_MAX_MENTIONS_PER_MESSAGE)

    def test_no_mentions_returns_empty(self):
        self.assertEqual(collect_mention_ids(make_message()), [])


class BuildReplyTestCase(unittest.TestCase):
    def test_single_entry_has_mention_and_reason(self):
        member = FakeMember(user_id=200)
        text = build_reply([(member, afk_row(200, "обед"))])

        self.assertIn("<@200>", text)
        self.assertIn("обед", text)

    def test_multiple_entries_in_one_text(self):
        first = FakeMember(user_id=200)
        second = FakeMember(user_id=201)
        text = build_reply([(first, afk_row(200, "обед")), (second, afk_row(201, "сон"))])

        self.assertIn("<@200>", text)
        self.assertIn("<@201>", text)

    def test_reason_markup_is_escaped(self):
        member = FakeMember(user_id=200)
        text = build_reply([(member, afk_row(200, "@everyone **бам**"))])

        self.assertNotIn("@everyone", text)

    def test_length_is_bounded(self):
        entries = [(FakeMember(user_id=i), afk_row(i, "x" * 500)) for i in range(200, 210)]

        self.assertLessEqual(len(build_reply(entries)), 1900)


class OnMessageTestCase(unittest.IsolatedAsyncioTestCase):
    """Поведение слушателя on_message."""

    def setUp(self):
        ratelimit.reset()
        self.addCleanup(ratelimit.reset)
        self.bot = MagicMock()
        self.cog = AfkEventsCog(self.bot)

    async def _dispatch(self, message, *, afk_rows=None, reserve=True):
        with patch(
            "afk.events.async_get_afk_users", new_callable=AsyncMock, return_value=afk_rows or {}
        ) as mock_rows:
            with patch(
                "afk.events.async_check_and_reply", new_callable=AsyncMock, return_value=reserve
            ) as mock_reserve:
                with patch("afk.events.async_cancel_reply") as mock_cancel:
                    await self.cog.on_message(message)
        return mock_rows, mock_reserve, mock_cancel

    async def test_bot_message_ignored(self):
        message = make_message(author=FakeMember(user_id=9, bot=True))

        mock_rows, _, _ = await self._dispatch(message)

        mock_rows.assert_not_called()
        message.channel.send.assert_not_awaited()

    async def test_dm_ignored(self):
        message = make_message()
        message.guild = None

        mock_rows, _, _ = await self._dispatch(message)

        mock_rows.assert_not_called()

    async def test_command_message_skips_autoreply(self):
        message = make_message(
            content=f"{config.CMD_PREFIX}afk_check", mentions=[FakeMember(user_id=200)]
        )

        mock_rows, _, _ = await self._dispatch(message)

        mock_rows.assert_not_called()
        message.channel.send.assert_not_awaited()

    async def test_no_mentions_no_db_query(self):
        mock_rows, _, _ = await self._dispatch(make_message())

        mock_rows.assert_not_called()

    async def test_mentioned_user_not_afk(self):
        message = make_message(mentions=[FakeMember(user_id=200)])

        await self._dispatch(message, afk_rows={})

        message.channel.send.assert_not_awaited()

    async def test_afk_user_gets_single_reply(self):
        target = FakeMember(user_id=200)
        message = make_message(mentions=[target])

        _, mock_reserve, _ = await self._dispatch(message, afk_rows={200: afk_row(200)})

        message.channel.send.assert_awaited_once()
        mock_reserve.assert_called_once_with(100, 200, 123)
        text = message.channel.send.await_args.args[0]
        self.assertIn("<@200>", text)

    async def test_several_afk_users_aggregated_into_one_message(self):
        """N упоминаний — один вызов Discord API."""
        mentions = [FakeMember(user_id=200), FakeMember(user_id=201), FakeMember(user_id=202)]
        message = make_message(mentions=mentions)
        rows = {200: afk_row(200), 201: afk_row(201), 202: afk_row(202)}

        await self._dispatch(message, afk_rows=rows)

        message.channel.send.assert_awaited_once()
        text = message.channel.send.await_args.args[0]
        for user_id in (200, 201, 202):
            self.assertIn(f"<@{user_id}>", text)

    async def test_reply_pings_only_afk_users(self):
        target = FakeMember(user_id=200)
        message = make_message(mentions=[target])

        await self._dispatch(message, afk_rows={200: afk_row(200)})

        allowed = message.channel.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(allowed.everyone)
        self.assertFalse(allowed.roles)
        self.assertEqual(allowed.users, [target])

    async def test_cooldown_blocks_reply(self):
        message = make_message(mentions=[FakeMember(user_id=200)])

        await self._dispatch(message, afk_rows={200: afk_row(200)}, reserve=False)

        message.channel.send.assert_not_awaited()

    async def test_channel_window_limits_replies(self):
        """Поток упоминаний в одном канале не превращается в спам."""
        sent = 0
        for _ in range(config.AFK_REPLY_CHANNEL_LIMIT + 3):
            message = make_message(mentions=[FakeMember(user_id=200)])
            await self._dispatch(message, afk_rows={200: afk_row(200)})
            sent += message.channel.send.await_count

        self.assertEqual(sent, config.AFK_REPLY_CHANNEL_LIMIT)

    async def test_send_failure_releases_reservation(self):
        """Ответ не ушёл — кулдаун не должен «съесть» право на автоответ."""
        message = make_message(mentions=[FakeMember(user_id=200)])
        message.channel.send = AsyncMock(side_effect=make_forbidden())

        _, _, mock_cancel = await self._dispatch(message, afk_rows={200: afk_row(200)})

        mock_cancel.assert_called_once_with(100, 200, 123)

    async def test_http_error_releases_all_reservations(self):
        mentions = [FakeMember(user_id=200), FakeMember(user_id=201)]
        message = make_message(mentions=mentions)
        message.channel.send = AsyncMock(side_effect=http_exception())
        rows = {200: afk_row(200), 201: afk_row(201)}

        _, _, mock_cancel = await self._dispatch(message, afk_rows=rows)

        self.assertEqual(mock_cancel.call_count, 2)

    async def test_stale_row_without_mention_object_releases_reservation(self):
        """В БД есть запись, но объекта участника в сообщении нет."""
        message = make_message(mentions=[FakeMember(user_id=200)])
        rows = {200: afk_row(200), 999: afk_row(999)}

        _, _, mock_cancel = await self._dispatch(message, afk_rows=rows)

        mock_cancel.assert_called_once_with(100, 999, 123)
        message.channel.send.assert_awaited_once()

    async def test_activity_does_not_remove_afk(self):
        """Спецификация: сообщение автора-AFK не снимает его статус."""
        author = FakeMember(user_id=100)
        message = make_message(author=author, mentions=[FakeMember(user_id=200)])

        with patch("afk.models.take_afk_session") as mock_take:
            await self._dispatch(message, afk_rows={200: afk_row(200)})

        mock_take.assert_not_called()


class ListenerRegistrationTestCase(unittest.IsolatedAsyncioTestCase):
    """Listener вместо @bot.event сохраняет работу команд."""

    async def asyncSetUp(self):
        self.bot = commands.Bot(command_prefix=config.CMD_PREFIX, intents=discord.Intents.none())
        self.addAsyncCleanup(self.bot.close)

    async def test_setup_registers_cog_listener(self):
        cog = await setup_afk_events(self.bot)

        self.assertIsInstance(cog, AfkEventsCog)
        listeners = [name for name, _ in self.bot.extra_events.items()]
        self.assertIn("on_message", listeners)

    async def test_setup_is_idempotent(self):
        first = await setup_afk_events(self.bot)
        second = await setup_afk_events(self.bot)

        self.assertIs(first, second)
        self.assertEqual(len(self.bot.extra_events.get("on_message", [])), 1)

    async def test_bot_on_message_not_overridden(self):
        """process_commands остаётся за штатным on_message клиента."""
        await setup_afk_events(self.bot)

        self.assertFalse(hasattr(self.bot, "_afk_on_message"))
        self.assertEqual(
            self.bot.on_message.__func__, commands.Bot.on_message, "штатный on_message заменён"
        )

    async def test_unload_removes_listener(self):
        await setup_afk_events(self.bot)
        await self.bot.remove_cog("AfkEventsCog")

        self.assertEqual(len(self.bot.extra_events.get("on_message", [])), 0)


if __name__ == "__main__":
    unittest.main()
