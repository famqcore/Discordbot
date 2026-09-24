"""Общая инфраструктура тестов: временная БД и асинхронные фейки Discord.

Тесты требуют контрактных тестов на объектах, которые ведут себя как
настоящие: корутинные методы возвращают awaitable, а не MagicMock, иначе
``RuntimeWarning: coroutine was never awaited`` маскирует реальные ошибки.

Здесь же собрана работа с временной базой: раньше каждый модуль тестов
подменял ``config.DB_PATH`` и перезагружал модули через ``importlib``.
Шлюз ``database.db`` сам отслеживает смену пути, поэтому reload не нужен —
достаточно указать путь и закрыть шлюз после теста.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

import config
from database import db as db_module
from database.migrations import migrate_schema

__all__ = [
    "AsyncIterator",
    "label_of",
    "DatabaseTestCase",
    "FakeChannel",
    "FakeGuild",
    "FakeInteraction",
    "FakeMember",
    "FakeMessage",
    "FakeThread",
    "http_exception",
    "make_forbidden",
    "make_not_found",
    "use_temp_database",
]


def label_of(item) -> str | None:
    """Подпись элемента формы без обращения к устаревшему свойству.

    ``discord.ui.TextInput.label`` помечен deprecated в discord.py 2.6+,
    а тесты запускаются с DeprecationWarning как ошибкой.
    Значение читается из нижележащего компонента, который и хранит его.
    """
    underlying = getattr(item, "_underlying", None)
    if underlying is not None and hasattr(underlying, "label"):
        return underlying.label
    return getattr(item, "label", None)


# --------------------------------------------------------------------------
# база данных
# --------------------------------------------------------------------------


def use_temp_database(test_case: unittest.TestCase) -> str:
    """Переключает ``config.DB_PATH`` на временный файл со свежей схемой.

    Регистрирует очистку: шлюз закрывается, путь возвращается, файл удаляется.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    previous = config.DB_PATH

    db_module.shutdown()
    config.DB_PATH = handle.name
    migrate_schema()

    def cleanup() -> None:
        db_module.shutdown()
        config.DB_PATH = previous
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(handle.name + suffix)
            except OSError:
                pass

    test_case.addCleanup(cleanup)
    return handle.name


class DatabaseTestCase(unittest.TestCase):
    """Синхронный тест-кейс с изолированной временной базой."""

    def setUp(self) -> None:
        super().setUp()
        self.db_path = use_temp_database(self)


class AsyncDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """Асинхронный тест-кейс с изолированной временной базой."""

    def setUp(self) -> None:
        super().setUp()
        self.db_path = use_temp_database(self)


# --------------------------------------------------------------------------
# исключения discord.py
# --------------------------------------------------------------------------


def _response(status: int) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.reason = "test"
    return response


def make_not_found(message: str = "not found") -> discord.NotFound:
    return discord.NotFound(_response(404), message)


def make_forbidden(message: str = "forbidden") -> discord.Forbidden:
    return discord.Forbidden(_response(403), message)


def http_exception(status: int = 500, message: str = "server error") -> discord.HTTPException:
    return discord.HTTPException(_response(status), message)


# --------------------------------------------------------------------------
# асинхронные фейки Discord
# --------------------------------------------------------------------------


class AsyncIterator:
    """Асинхронный итератор для history()/archived_threads()."""

    def __init__(self, items=()):
        self._items = list(items)

    def __aiter__(self) -> AsyncIterator:
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:  # pragma: no cover - протокол итератора
            raise StopAsyncIteration from None

    def __call__(self, *args, **kwargs) -> AsyncIterator:
        """Позволяет использовать объект и как метод, и как итератор."""
        return AsyncIterator(self._items)


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int = 1,
        content: str = "",
        author=None,
        created_at=None,
        embeds=(),
        attachments=(),
    ) -> None:
        self.id = message_id
        self.content = content
        self.clean_content = content
        self.author = author or FakeMember()
        self.created_at = created_at
        self.embeds = list(embeds)
        self.attachments = list(attachments)
        self.delete = AsyncMock()
        self.edit = AsyncMock()
        self.reply = AsyncMock()


class FakeMember:
    def __init__(
        self,
        *,
        user_id: int = 100,
        name: str = "user",
        nick: str | None = None,
        bot: bool = False,
        guild=None,
        roles=(),
    ) -> None:
        self.id = user_id
        self.name = name
        self.display_name = nick or name
        self.nick = nick
        self.bot = bot
        self.guild = guild
        self.roles = list(roles)
        self.mention = f"<@{user_id}>"
        self.send = AsyncMock()
        self.edit = AsyncMock()


class FakeChannel:
    def __init__(
        self,
        *,
        channel_id: int = 200,
        name: str = "channel",
        guild=None,
        messages=(),
        threads=(),
        archived=(),
    ) -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.mention = f"<#{channel_id}>"
        self.threads = list(threads)
        self._archived = list(archived)
        self.send = AsyncMock(return_value=FakeMessage())
        self.delete = AsyncMock()
        self.edit = AsyncMock()
        self.create_thread = AsyncMock()
        self.set_permissions = AsyncMock()
        self._messages = list(messages)

    def history(self, *args, **kwargs) -> AsyncIterator:
        messages = list(self._messages)
        if not kwargs.get("oldest_first", False):
            messages.reverse()
        limit = kwargs.get("limit")
        if isinstance(limit, int):
            messages = messages[:limit]
        return AsyncIterator(messages)

    def archived_threads(self, *args, **kwargs) -> AsyncIterator:
        return AsyncIterator(self._archived)


class FakeThread:
    def __init__(
        self,
        *,
        thread_id: int = 300,
        name: str = "thread",
        parent=None,
        archived: bool = False,
        locked: bool = False,
    ) -> None:
        self.id = thread_id
        self.name = name
        self.parent = parent
        self.parent_id = getattr(parent, "id", None)
        self.archived = archived
        self.locked = locked
        self.send = AsyncMock(return_value=FakeMessage())
        self.edit = AsyncMock()
        self.fetch_message = AsyncMock(return_value=FakeMessage())


class FakeGuild:
    def __init__(self, *, guild_id: int = 1, name: str = "Guild", channels=(), members=()) -> None:
        self.id = guild_id
        self.name = name
        self.me = FakeMember(user_id=999, name="bot", bot=True)
        self.me.top_role = MagicMock()
        self.default_role = MagicMock(name="@everyone")
        self._channels = {channel.id: channel for channel in channels}
        self._members = {member.id: member for member in members}
        self.roles = []
        self.categories = []
        self.voice_channels = []
        self.text_channels = [c for c in channels if isinstance(c, FakeChannel)]
        self.create_text_channel = AsyncMock()
        self.fetch_channel = AsyncMock(side_effect=make_not_found())

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    def get_member(self, user_id: int):
        return self._members.get(user_id)

    def get_role(self, role_id: int):
        return next((role for role in self.roles if role.id == role_id), None)

    def add_channel(self, channel) -> None:
        self._channels[channel.id] = channel

    def add_member(self, member) -> None:
        member.guild = self
        self._members[member.id] = member


class FakeResponse:
    """discord.InteractionResponse с настоящим состоянием done()."""

    def __init__(self) -> None:
        self._done = False
        self.send_message = AsyncMock(side_effect=self._mark_done)
        self.defer = AsyncMock(side_effect=self._mark_done)
        self.send_modal = AsyncMock(side_effect=self._mark_done)
        self.edit_message = AsyncMock(side_effect=self._mark_done)

    async def _mark_done(self, *args, **kwargs) -> None:
        self._done = True

    def is_done(self) -> bool:
        return self._done


class FakeInteraction:
    def __init__(self, *, user=None, guild=None, channel=None) -> None:
        self.guild = guild if guild is not None else FakeGuild()
        self.user = user if user is not None else FakeMember()
        self.channel = channel
        self.guild_id = getattr(self.guild, "id", None)
        self.response = FakeResponse()
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.client = MagicMock()
        self.message = None

    def sent_texts(self) -> list[str]:
        """Все тексты, отправленные пользователю (response и followup)."""
        texts = []
        for mock in (self.response.send_message, self.followup.send):
            for call in mock.await_args_list:
                if call.args:
                    texts.append(str(call.args[0]))
                elif "content" in call.kwargs:
                    texts.append(str(call.kwargs["content"]))
        return texts
