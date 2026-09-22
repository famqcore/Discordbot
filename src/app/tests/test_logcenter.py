import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

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
    channel.create_thread = AsyncMock()
    channel.edit = AsyncMock()
    return channel


def make_thread(guild, channel, thread_id=200, *, archived=False):
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.guild = guild
    thread.parent_id = channel.id
    thread.archived = archived
    thread.name = "ветка"
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
        mock_state.get_state = MagicMock(return_value=None)
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
        mock_state.get_state = MagicMock(return_value=None)

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_awaited_once()
        channel.create_thread.assert_awaited_once()
        stored_keys = [call.args[0] for call in mock_state.set_state.call_args_list]
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
        mock_state.get_state = MagicMock(
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
        mock_state.get_state = MagicMock(
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
        mock_state.get_state = MagicMock(return_value="100")  # ID, которого уже нет

        with patch.object(logcenter, "config", make_config()):
            with patch.object(logcenter, "state_db", mock_state):
                result = await send_to_log(guild, "afk", content="test")

        self.assertIsNotNone(result)
        guild.create_text_channel.assert_awaited_once()


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
