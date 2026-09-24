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


class TestManagedLogCenter(unittest.IsolatedAsyncioTestCase):
    """Лог-центр, созданный ботом: приватный, ID запоминаются, дрейф прав чинится."""

    async def test_creates_private_channel_and_thread_and_remembers_ids(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        channel.create_thread = AsyncMock(return_value=thread)
        guild.create_text_channel = AsyncMock(return_value=channel)

        mock_state = MagicMock()
        mock_state.async_set_state = AsyncMock()
        mock_state.async_delete_state = AsyncMock()
        mock_state.async_get_state = AsyncMock(return_value=None)

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_awaited_once()
        channel.create_thread.assert_awaited_once()
        stored_keys = [call.args[0] for call in mock_state.async_set_state.call_args_list]
        self.assertIn(f"log_channel:{guild.id}", stored_keys)
        self.assertIn(f"log_thread:{guild.id}:afk", stored_keys)

    async def test_reuses_channel_from_state(self):
        guild = make_guild()
        channel = make_channel(guild)
        thread = make_thread(guild, channel)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))
        guild.get_thread = MagicMock(side_effect=lambda cid: {200: thread}.get(cid))
        guild.create_text_channel = AsyncMock()

        mock_state = MagicMock()
        mock_state.async_set_state = AsyncMock()
        mock_state.async_delete_state = AsyncMock()
        mock_state.async_get_state = AsyncMock(
            side_effect=lambda key: "100" if key == f"log_channel:{guild.id}" else "200"
        )

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_not_called()

    async def test_repairs_overwrites_of_managed_channel(self):
        guild = make_guild()
        # канал наш, но кто-то открыл его публично — права приводим к приватным
        channel = make_channel(guild, viewable_by_everyone=True)
        thread = make_thread(guild, channel)
        channel.create_thread = AsyncMock(return_value=thread)
        guild.get_channel = MagicMock(side_effect=lambda cid: {100: channel}.get(cid))

        mock_state = MagicMock()
        mock_state.async_set_state = AsyncMock()
        mock_state.async_delete_state = AsyncMock()
        mock_state.async_get_state = AsyncMock(
            side_effect=lambda key: "100" if key == f"log_channel:{guild.id}" else None
        )

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        channel.edit.assert_awaited_once()

    async def test_stale_state_channel_recreates(self):
        guild = make_guild()
        new_channel = make_channel(guild)
        thread = make_thread(guild, new_channel)
        new_channel.create_thread = AsyncMock(return_value=thread)
        guild.create_text_channel = AsyncMock(return_value=new_channel)

        mock_state = MagicMock()
        mock_state.async_set_state = AsyncMock()
        mock_state.async_delete_state = AsyncMock()
        mock_state.async_get_state = AsyncMock(return_value="100")  # ID, которого уже нет

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_awaited_once()


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

        with patch.object(logcenter, "config", make_config()):
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

        with patch.object(logcenter, "config", make_config()):
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

        with patch.object(logcenter, "config", make_config()):
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

        with patch.object(logcenter, "config", make_config()):
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
