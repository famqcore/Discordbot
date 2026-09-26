import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tests.support import AsyncIterator, http_exception, make_forbidden, make_not_found
from utils import logcenter
from utils.logcenter import (
    audit_channel_privacy,
    delete_log_messages,
    send_to_log,
    validate_log_center_config,
)


def make_config(**overrides):
    base = {
        "LOG_CHANNEL_ID": None,
        "LOG_CHANNEL_NAME": "📋-логи",
        "LOG_THREAD_IDS": {"afk": None, "decisions": None},
        "LOG_THREAD_NAMES": {"afk": "🔴-afk", "decisions": "⚖️-решения"},
        "LOG_SECTION_NAMES": {"afk": "🔴-afk", "decisions": "⚖️-решения", "errors": "🚨-ошибки"},
        "LOG_CATEGORY_NAME": "logs bot",
        "LOG_ERROR_PING_ENABLED": True,
        "LOG_ERROR_PING_GUILD_OWNER": True,
        "LOG_ERROR_PING_TEXT": "{mentions} ошибка бота, нужна проверка",
        "LOG_ERRORS_GUIDE": "Как сообщать об ошибках бота",
        "LOG_KEY_ERRORS": "errors",
        "ROLE_OWNER_ID": 11,
        "ROLE_DEP_OWNER_ID": 12,
        "LOG_THREAD_ENV_NAMES": {
            "afk": "LOG_THREAD_AFK_ID",
            "decisions": "LOG_THREAD_DECISIONS_ID",
        },
        "STAFF_ROLE_IDS": [10, 20],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_guild(guild_id=1):
    guild = MagicMock()
    guild.id = guild_id
    guild.name = "Тестовый сервер"
    guild.owner_id = 777
    guild.me = MagicMock()
    guild.me.id = 999
    guild.me.roles = []
    guild.default_role = MagicMock(spec=discord.Role)
    guild.get_role = MagicMock(return_value=None)
    guild.get_channel = MagicMock(return_value=None)
    guild.get_thread = MagicMock(return_value=None)
    # объекта нет ни в кэше, ни на сервере: fetch_channel обязан быть
    # корутиной, иначе тест не поймает «await MagicMock»
    guild.fetch_channel = AsyncMock(side_effect=make_not_found())
    return guild


def make_channel(guild, channel_id=100, *, viewable_by_everyone=False):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.guild = guild
    channel.name = "лог-канал"
    channel.mention = f"<#{channel_id}>"
    channel.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    channel.permissions_for = MagicMock(
        return_value=SimpleNamespace(view_channel=viewable_by_everyone)
    )
    channel.overwrites = {}
    channel.threads = []
    channel.archived_threads = MagicMock(return_value=AsyncIterator([]))
    channel.create_thread = AsyncMock()
    channel.edit = AsyncMock()
    return channel


def make_thread(guild, channel, thread_id=200, *, archived=False, locked=False, name="ветка"):
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.guild = guild
    thread.parent = channel
    thread.parent_id = channel.id
    thread.archived = archived
    thread.locked = locked
    thread.name = name
    thread.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    thread.edit = AsyncMock()
    thread.fetch_message = AsyncMock()
    return thread


def make_role(role_id, name="Роль"):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.is_default = MagicMock(return_value=False)
    return role


class TestSendToLog(unittest.IsolatedAsyncioTestCase):
    """send_to_log: fail closed — небезопасная точка не получает данные."""

    async def test_none_guild_returns_none(self):
        self.assertIsNone(await send_to_log(None, "afk", content="test"))

    async def test_external_channel_and_thread_accepted(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        thread.send.assert_awaited_once()
        # пинги из логов запрещены даже служебные
        kwargs = thread.send.call_args.kwargs
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertEqual(kwargs["allowed_mentions"].roles, [])

    async def test_critical_delivery_propagates_discord_error(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        thread.send = AsyncMock(side_effect=http_exception(503))
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            with self.assertRaises(discord.HTTPException):
                await send_to_log(guild, "afk", content="test", raise_http_errors=True)

    async def test_public_channel_refused(self):
        guild = make_guild()
        channel = make_channel(guild, viewable_by_everyone=True)
        guild.get_channel = MagicMock(return_value=channel)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)
        channel.send.assert_not_called()

    async def test_wrong_channel_type_refused(self):
        guild = make_guild()
        voice = MagicMock(spec=discord.VoiceChannel)
        voice.id = 100
        voice.guild = guild
        guild.get_channel = MagicMock(return_value=voice)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)

    async def test_channel_from_other_guild_refused(self):
        guild = make_guild(guild_id=1)
        other_guild = make_guild(guild_id=2)
        channel = make_channel(other_guild)
        guild.get_channel = MagicMock(return_value=channel)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)

    async def test_unapproved_role_overwrite_refused(self):
        guild = make_guild()
        channel = make_channel(guild)
        outsider = make_role(777, "Посторонняя")
        channel.overwrites = {outsider: discord.PermissionOverwrite(view_channel=True)}
        guild.get_channel = MagicMock(return_value=channel)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)

    async def test_staff_role_and_bot_overwrites_accepted(self):
        guild = make_guild()
        channel = make_channel(guild)
        staff = make_role(10, "Staff")
        bot_member = MagicMock()
        bot_member.id = guild.me.id
        channel.overwrites = {
            staff: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            bot_member: discord.PermissionOverwrite(view_channel=True),
        }
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        thread = make_thread(guild, channel)
        channel.create_thread = AsyncMock(return_value=thread)

        mock_state = MagicMock()
        mock_state.async_set_state = AsyncMock()
        mock_state.async_delete_state = AsyncMock()
        mock_state.async_get_state = AsyncMock(return_value=None)
        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)

    async def test_thread_with_foreign_parent_refused(self):
        guild = make_guild()
        channel = make_channel(guild)
        other_channel = make_channel(guild, channel_id=300)
        thread = make_thread(guild, other_channel, thread_id=200)
        guild.get_channel = MagicMock(
            side_effect=lambda cid: {100: channel, 300: other_channel}.get(cid)
        )
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)
        thread.send.assert_not_called()

    async def test_archived_thread_is_unarchived(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel, archived=True)
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        thread.edit.assert_awaited_once()

    async def test_unarchive_failure_refused(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel, archived=True)
        thread.edit = AsyncMock(side_effect=Exception("нет прав"))
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)
        thread.send.assert_not_called()

    async def test_missing_configured_channel_refused_no_fallback(self):
        guild = make_guild()
        # канал не найден нигде: раньше бот молча слал «по имени» или в корень
        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            result = await send_to_log(guild, "afk", content="секрет")

        self.assertIsNone(result)

    async def test_send_exception_returns_none_without_raising(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        thread.send = AsyncMock(side_effect=Exception("API упал"))
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNone(result)


def make_category(guild, category_id=50, *, viewable_by_everyone=False, channels=()):
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = category_id
    category.guild = guild
    category.name = "logs bot"
    category.channels = list(channels)
    category.edit = AsyncMock()
    category.permissions_for = MagicMock(
        return_value=SimpleNamespace(view_channel=viewable_by_everyone)
    )
    return category


class TestManagedLogCenter(unittest.IsolatedAsyncioTestCase):
    """Управляемый лог-центр: категория «logs bot» и отдельный канал на раздел."""

    def _state(self, values=None):
        stored = dict(values or {})
        state = MagicMock()
        state.calls = stored
        state.async_get_state = AsyncMock(side_effect=lambda key: stored.get(key))
        state.async_set_state = AsyncMock(
            side_effect=lambda key, value: stored.__setitem__(key, value)
        )
        state.async_delete_state = AsyncMock(side_effect=lambda key: stored.pop(key, None))
        return state

    async def test_creates_category_and_channel_and_remembers_ids(self):
        guild = make_guild()
        category = make_category(guild)
        channel = make_channel(guild)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(return_value=channel)
        state = self._state()

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_category.assert_awaited_once()
        guild.create_text_channel.assert_awaited_once()
        # канал создаётся внутри категории и закрыт для @everyone
        kwargs = guild.create_text_channel.call_args.kwargs
        self.assertIs(kwargs["category"], category)
        self.assertIn("overwrites", kwargs)
        self.assertEqual(state.calls[f"log_category:{guild.id}"], str(category.id))
        self.assertEqual(state.calls[f"log_channel:{guild.id}:afk"], str(channel.id))
        channel.send.assert_awaited_once()

    async def test_separate_channel_per_section(self):
        guild = make_guild()
        category = make_category(guild)
        afk_channel = make_channel(guild, channel_id=101)
        decisions_channel = make_channel(guild, channel_id=102)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(side_effect=[afk_channel, decisions_channel])
        state = self._state()
        known = {50: category, 101: afk_channel, 102: decisions_channel}
        guild.get_channel = MagicMock(side_effect=known.get)

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                await send_to_log(guild, "afk", content="раз")
                await send_to_log(guild, "decisions", content="два")

        self.assertEqual(guild.create_category.await_count, 1)
        self.assertEqual(guild.create_text_channel.await_count, 2)
        self.assertEqual(state.calls[f"log_channel:{guild.id}:afk"], "101")
        self.assertEqual(state.calls[f"log_channel:{guild.id}:decisions"], "102")

    async def test_reuses_channel_from_state(self):
        guild = make_guild()
        channel = make_channel(guild)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.create_text_channel = AsyncMock()
        guild.create_category = AsyncMock()
        state = self._state({f"log_channel:{guild.id}:afk": "100"})

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_not_called()
        guild.create_category.assert_not_called()

    async def test_existing_channel_in_category_reused_by_name(self):
        """Потеря bot_state не плодит дубли: канал ищется по имени в категории."""
        guild = make_guild()
        existing = make_channel(guild, channel_id=111)
        existing.name = "🔴-afk"
        category = make_category(guild, channels=[existing])
        guild.get_channel = MagicMock(side_effect=lambda cid: {50: category}.get(cid))
        guild.create_text_channel = AsyncMock()
        state = self._state({f"log_category:{guild.id}": "50"})

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_not_called()
        self.assertEqual(state.calls[f"log_channel:{guild.id}:afk"], "111")

    async def test_repairs_overwrites_of_managed_channel(self):
        guild = make_guild()
        # канал наш, но кто-то открыл его публично: права приводим к приватным
        channel = make_channel(guild, viewable_by_everyone=True)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        state = self._state({f"log_channel:{guild.id}:afk": "100"})

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.edit.assert_awaited_once()

    async def test_repairs_public_category(self):
        guild = make_guild()
        category = make_category(guild, viewable_by_everyone=True)
        channel = make_channel(guild)
        guild.get_channel = MagicMock(side_effect=lambda cid: {50: category}.get(cid))
        guild.create_text_channel = AsyncMock(return_value=channel)
        state = self._state({f"log_category:{guild.id}": "50"})

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        category.edit.assert_awaited_once()

    async def test_stale_state_channel_recreates(self):
        guild = make_guild()
        category = make_category(guild)
        new_channel = make_channel(guild)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(return_value=new_channel)
        # ID, которых уже нет на сервере
        state = self._state(
            {f"log_channel:{guild.id}:afk": "100", f"log_category:{guild.id}": "50"}
        )

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_awaited_once()

    async def test_errors_channel_gets_bug_report_guide(self):
        guild = make_guild()
        category = make_category(guild)
        channel = make_channel(guild)
        guide_message = MagicMock(spec=discord.Message)
        guide_message.pin = AsyncMock()
        channel.send = AsyncMock(return_value=guide_message)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(return_value=channel)

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", self._state()):
                await send_to_log(guild, "errors", content="упало")

        texts = [
            call.args[0] if call.args else call.kwargs.get("content")
            for call in channel.send.call_args_list
        ]
        self.assertIn("Как сообщать об ошибках бота", texts[0])
        guide_message.pin.assert_awaited_once()

    async def test_error_message_pings_owners(self):
        guild = make_guild()
        category = make_category(guild)
        channel = make_channel(guild)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(return_value=channel)

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", self._state()):
                await send_to_log(guild, "errors", content="упало")

        last = channel.send.call_args
        content = last.kwargs["content"]
        self.assertIn("<@&11>", content)
        self.assertIn("<@&12>", content)
        self.assertIn("<@777>", content)
        allowed = last.kwargs["allowed_mentions"]
        self.assertEqual([role.id for role in allowed.roles], [11, 12])
        self.assertEqual([user.id for user in allowed.users], [777])

    async def test_ping_disabled_keeps_content_clean(self):
        guild = make_guild()
        category = make_category(guild)
        channel = make_channel(guild)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(return_value=channel)

        with patch.object(logcenter, "config", make_config(LOG_ERROR_PING_ENABLED=False)):
            with patch.object(logcenter, "state_db", self._state()):
                await send_to_log(guild, "errors", content="упало")

        content = channel.send.call_args.kwargs["content"]
        self.assertEqual(content, "упало")
        self.assertEqual(channel.send.call_args.kwargs["allowed_mentions"].roles, [])


class TestConcurrentThreadResolution(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_first_send_creates_one_thread(self):
        guild = make_guild()
        channel = make_channel(guild)
        created = make_thread(guild, channel, thread_id=200, name="🔴-afk")
        guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 100 else None)
        guild.get_thread = MagicMock(side_effect=lambda cid: created if cid == 200 else None)

        state_values = {}

        async def get_state(key):
            await asyncio.sleep(0)
            return state_values.get(key)

        async def set_state(key, value):
            state_values[key] = value

        async def create_thread(**kwargs):
            await asyncio.sleep(0)
            return created

        channel.create_thread = AsyncMock(side_effect=create_thread)
        state = MagicMock()
        state.async_get_state = AsyncMock(side_effect=get_state)
        state.async_set_state = AsyncMock(side_effect=set_state)
        state.async_delete_state = AsyncMock()

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", state):
                first, second = await asyncio.gather(
                    send_to_log(guild, "afk", content="первый"),
                    send_to_log(guild, "afk", content="второй"),
                )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        channel.create_thread.assert_awaited_once()
        self.assertEqual(created.send.await_count, 2)
        self.assertEqual(state_values[f"log_thread:{guild.id}:afk"], "200")


class TestArchivedThreadReuse(unittest.IsolatedAsyncioTestCase):
    """Архивированная ветка переиспользуется, а не дублируется."""

    def _managed_state(self, guild, channel_id="100"):
        state = MagicMock()
        state.async_set_state = AsyncMock()
        state.async_delete_state = AsyncMock()
        state.async_get_state = AsyncMock(
            side_effect=lambda key: channel_id if key == f"log_channel:{guild.id}" else None
        )
        return state

    async def test_archived_thread_found_by_name_and_reused(self):
        guild = make_guild()
        channel = make_channel(guild)
        archived = make_thread(guild, channel, thread_id=201, archived=True, name="🔴-afk")
        channel.archived_threads = MagicMock(return_value=AsyncIterator([archived]))
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        state = self._managed_state(guild)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.create_thread.assert_not_called()
        archived.edit.assert_awaited_once_with(archived=False)
        archived.send.assert_awaited_once()
        state.async_set_state.assert_any_call(f"log_thread:{guild.id}:afk", "201")

    async def test_active_thread_preferred_over_archive_scan(self):
        guild = make_guild()
        channel = make_channel(guild)
        active = make_thread(guild, channel, thread_id=202, name="🔴-afk")
        channel.threads = [active]
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", self._managed_state(guild)):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.create_thread.assert_not_called()
        channel.archived_threads.assert_not_called()
        active.send.assert_awaited_once()

    async def test_thread_with_other_name_not_reused(self):
        guild = make_guild()
        channel = make_channel(guild)
        other = make_thread(guild, channel, thread_id=203, name="другая-ветка")
        channel.threads = [other]
        created = make_thread(guild, channel, thread_id=204, name="🔴-afk")
        channel.create_thread = AsyncMock(return_value=created)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", self._managed_state(guild)):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.create_thread.assert_awaited_once()
        other.send.assert_not_called()

    async def test_archive_scan_failure_falls_back_to_creation(self):
        guild = make_guild()
        channel = make_channel(guild)
        channel.archived_threads = MagicMock(side_effect=make_forbidden())
        created = make_thread(guild, channel, thread_id=205, name="🔴-afk")
        channel.create_thread = AsyncMock(return_value=created)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter, "state_db", self._managed_state(guild)):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.create_thread.assert_awaited_once()

    async def test_locked_thread_rejected(self):
        guild = make_guild()
        channel = make_channel(guild)
        locked = make_thread(guild, channel, thread_id=206, locked=True)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.get_thread = MagicMock(side_effect=lambda cid: {206: locked}.get(cid))

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 206})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNone(result)
        locked.send.assert_not_called()

    async def test_thread_of_other_parent_rejected(self):
        guild = make_guild()
        channel = make_channel(guild)
        foreign_parent = make_channel(guild, channel_id=555)
        foreign = make_thread(guild, foreign_parent, thread_id=207)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.get_thread = MagicMock(side_effect=lambda cid: {207: foreign}.get(cid))

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 207})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNone(result)
        foreign.send.assert_not_called()

    async def test_non_thread_destination_rejected(self):
        """Обычный канал вместо ветки — не точка доставки логов."""
        guild = make_guild()
        channel = make_channel(guild)
        impostor = make_channel(guild, channel_id=208)
        guild.get_channel = MagicMock(
            side_effect=lambda cid: {100: channel, 208: impostor}.get(cid)
        )

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 208})
        ):
            result = await send_to_log(guild, "afk", content="test")

        self.assertIsNone(result)
        impostor.send.assert_not_called()


class TestDeliveryCounters(unittest.IsolatedAsyncioTestCase):
    """Потери аудита видны в счётчиках."""

    def setUp(self):
        logcenter.reset_delivery_stats()
        self.addCleanup(logcenter.reset_delivery_stats)

    async def _send_ok(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.get_thread = MagicMock(side_effect=lambda cid: {200: thread}.get(cid))
        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            return await send_to_log(guild, "afk", content="test")

    async def test_successful_send_counted(self):
        await self._send_ok()

        self.assertEqual(logcenter.delivery_stats()["sent"], 1)
        self.assertEqual(logcenter.delivery_stats()["failed"], 0)

    async def test_rejected_destination_counted(self):
        guild = make_guild()

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            await send_to_log(guild, "afk", content="test")

        self.assertEqual(logcenter.delivery_stats()["rejected"], 1)

    async def test_api_failure_counted(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        thread.send = AsyncMock(side_effect=http_exception())
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.get_thread = MagicMock(side_effect=lambda cid: {200: thread}.get(cid))

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            await send_to_log(guild, "afk", content="test")

        self.assertEqual(logcenter.delivery_stats()["failed"], 1)

    async def test_repeated_failures_raise_critical_alert(self):
        guild = make_guild()

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter.logger, "critical") as mock_critical:
                for _ in range(logcenter.DEGRADED_ALERT_THRESHOLD):
                    await send_to_log(guild, "afk", content="test")

        mock_critical.assert_called()

    async def test_success_resets_failure_streak(self):
        guild = make_guild()
        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            await send_to_log(guild, "afk", content="test")

        await self._send_ok()

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            with patch.object(logcenter.logger, "critical") as mock_critical:
                await send_to_log(guild, "afk", content="test")

        mock_critical.assert_not_called()


class TestAuditChannelPrivacy(unittest.TestCase):
    def test_clean_channel_has_no_problems(self):
        guild = make_guild()
        channel = make_channel(guild)
        staff = make_role(10)
        channel.overwrites = {staff: discord.PermissionOverwrite(view_channel=True)}

        with patch.object(logcenter, "config", make_config()):
            self.assertEqual(audit_channel_privacy(guild, channel), [])

    def test_everyone_view_detected(self):
        guild = make_guild()
        channel = make_channel(guild, viewable_by_everyone=True)

        with patch.object(logcenter, "config", make_config()):
            problems = audit_channel_privacy(guild, channel)

        self.assertTrue(any("@everyone" in p for p in problems))

    def test_personal_member_access_detected(self):
        guild = make_guild()
        channel = make_channel(guild)
        member = MagicMock()
        member.id = 424242
        channel.overwrites = {member: discord.PermissionOverwrite(view_channel=True)}

        with patch.object(logcenter, "config", make_config()):
            problems = audit_channel_privacy(guild, channel)

        self.assertTrue(any("персональный доступ" in p for p in problems))


class TestValidateLogCenterConfig(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_is_ok(self):
        guild = make_guild()
        with patch.object(logcenter, "config", make_config()):
            self.assertEqual(await validate_log_center_config(guild), [])

    async def test_public_channel_reported(self):
        guild = make_guild()
        channel = make_channel(guild, viewable_by_everyone=True)
        guild.get_channel = MagicMock(return_value=channel)

        with patch.object(logcenter, "config", make_config(LOG_CHANNEL_ID=100)):
            problems = await validate_log_center_config(guild)

        self.assertTrue(any("LOG_CHANNEL_ID" in p for p in problems))

    async def test_thread_outside_log_channel_reported(self):
        guild = make_guild()
        channel = make_channel(guild)
        other_channel = make_channel(guild, channel_id=300)
        thread = make_thread(guild, other_channel, thread_id=200)
        guild.get_channel = MagicMock(
            side_effect=lambda cid: {100: channel, 300: other_channel}.get(cid)
        )
        guild.get_thread = MagicMock(side_effect=lambda cid: thread if cid == 200 else None)

        with patch.object(
            logcenter, "config", make_config(LOG_CHANNEL_ID=100, LOG_THREAD_IDS={"afk": 200})
        ):
            problems = await validate_log_center_config(guild)

        self.assertTrue(any("LOG_THREAD_AFK_ID" in p for p in problems))


class TestDeleteLogMessages(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_tracked_messages(self):
        guild = make_guild()
        thread = make_thread(guild, make_channel(guild))
        message = MagicMock()
        message.delete = AsyncMock()
        thread.fetch_message = AsyncMock(return_value=message)
        guild.get_thread = MagicMock(return_value=thread)

        deleted = await delete_log_messages(guild, [(200, 500), (200, 501)])

        self.assertEqual(deleted, 2)
        self.assertEqual(message.delete.await_count, 2)

    async def test_missing_thread_skipped(self):
        guild = make_guild()
        deleted = await delete_log_messages(guild, [(200, 500)])
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
