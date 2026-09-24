"""Транскрипт тикета: снимок переписки перед удалением канала.

Согласованный объём (отражён в docs/privacy.md и в тексте, который видит
модератор): это **ограниченный снимок** канала заявки, а не юридически
полный архив Discord.

Что попадает в файл:

- метаданные: сервер, канал, тикет, время выгрузки, число сообщений,
  признак усечения;
- сообщения в хронологическом порядке: время UTC, автор (имя и ID),
  текст (``clean_content`` — упоминания уже раскрыты в читаемый вид);
- содержимое embed'ов: заголовок, описание, поля;
- метаданные вложений: имя файла, размер, тип. Сами файлы и ссылки
  Discord CDN не сохраняются: ссылки протухают, а копирование файлов
  вышло бы за рамки согласованной retention-политики.

Чего в снимке нет и о чём честно написано в шапке файла: истории
редактирований, удалённых сообщений, реакций и сообщений сверх лимита
``MAX_MESSAGES``.

``build_transcript`` всегда возвращает результат: модератор узнаёт об
усечении и о частичной ошибке чтения ДО удаления канала.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import discord

from utils import clock
from utils.logger import logger

MAX_MESSAGES = 5000
DISCORD_ATTACHMENT_LIMIT_BYTES = 8 * 1024 * 1024
TRANSCRIPT_SAFETY_MARGIN_BYTES = 1024 * 1024
MAX_FILE_BYTES = DISCORD_ATTACHMENT_LIMIT_BYTES - TRANSCRIPT_SAFETY_MARGIN_BYTES
HEADER_SEPARATOR = "=" * 72


@dataclass
class Transcript:
    """Результат сборки снимка переписки."""

    channel_id: int
    message_count: int = 0
    truncated: bool = False
    failed: bool = False
    error: str | None = None
    size_limited: bool = False
    files: list[discord.File] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not (self.truncated or self.failed or self.size_limited)

    def status_note(self) -> str:
        """Текст для модератора и лога: что именно сохранено."""
        if self.failed:
            return (
                "⚠️ Транскрипт не собран: "
                f"{self.error or 'ошибка чтения истории канала'}. "
                "Переписка будет потеряна при удалении канала."
            )
        notes = [f"сообщений сохранено: {self.message_count}"]
        if self.truncated:
            notes.append(f"история обрезана до последних {MAX_MESSAGES}")
        if self.size_limited:
            notes.append("файл обрезан по размеру вложения Discord")
        if self.complete:
            return f"✅ Транскрипт полный ({notes[0]})."
        return "⚠️ Транскрипт частичный: " + "; ".join(notes) + "."


def _format_attachment(attachment) -> str:
    size = getattr(attachment, "size", None)
    content_type = getattr(attachment, "content_type", None) or "unknown"
    filename = getattr(attachment, "filename", "file")
    size_text = f"{size} байт" if isinstance(size, int) else "размер неизвестен"
    return f"    [вложение] {filename} ({content_type}, {size_text})"


def _format_embed(embed) -> list[str]:
    lines = []
    title = getattr(embed, "title", None)
    description = getattr(embed, "description", None)
    if title:
        lines.append(f"    [embed] {title}")
    if description:
        lines.append(f"    [embed] {description}")
    for embed_field in getattr(embed, "fields", []) or []:
        name = getattr(embed_field, "name", "")
        value = getattr(embed_field, "value", "")
        lines.append(f"    [embed:{name}] {value}")
    return lines


def _format_message(message) -> list[str]:
    created = getattr(message, "created_at", None)
    stamp = clock.to_utc(created).strftime("%Y-%m-%d %H:%M:%S UTC") if created else "—"
    author = getattr(message, "author", None)
    author_id = getattr(author, "id", "—")
    # display_name/name — стабильное представление; str(author) у объектов
    # без __str__ выводит repr с адресом памяти
    author_name = (
        getattr(author, "display_name", None) or getattr(author, "name", None) or str(author)
    )
    content = getattr(message, "clean_content", "") or ""

    lines = [f"[{stamp}] {author_name} ({author_id}): {content}".rstrip()]
    for embed in getattr(message, "embeds", []) or []:
        lines.extend(_format_embed(embed))
    for attachment in getattr(message, "attachments", []) or []:
        lines.append(_format_attachment(attachment))
    return lines


def _header(channel, message_count: int, truncated: bool) -> list[str]:
    guild = getattr(channel, "guild", None)
    return [
        HEADER_SEPARATOR,
        "ТРАНСКРИПТ ТИКЕТА (ограниченный снимок канала)",
        HEADER_SEPARATOR,
        f"Сервер: {getattr(guild, 'name', '—')} ({getattr(guild, 'id', '—')})",
        f"Канал: {getattr(channel, 'name', '—')} ({getattr(channel, 'id', '—')})",
        f"Выгружено: {clock.to_db()}",
        f"Сообщений в снимке: {message_count}",
        f"История обрезана: {'да, сохранены последние ' + str(MAX_MESSAGES) if truncated else 'нет'}",
        "",
        "В снимок входят: текст сообщений, содержимое embed'ов и метаданные",
        "вложений (имя, тип, размер). В снимок НЕ входят: файлы вложений,",
        "история правок, удалённые сообщения и реакции.",
        "Срок хранения снимка равен сроку хранения заявки (docs/privacy.md).",
        HEADER_SEPARATOR,
        "",
    ]


def _validate_message_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_MESSAGES:
        raise ValueError(f"limit должен быть в диапазоне от 1 до {MAX_MESSAGES}")
    return limit


async def build_transcript(channel: Any, limit: int = MAX_MESSAGES) -> Transcript:
    """Собирает ограниченный снимок переписки с диагностикой результата."""
    limit = _validate_message_limit(limit)
    channel_id = getattr(channel, "id", 0)
    result = Transcript(channel_id=channel_id)

    messages: list[Any] = []
    count = 0
    try:
        # Discord отдаёт историю от новых сообщений к старым. Берём свежий
        # хвост, а перед записью возвращаем хронологический порядок.
        async for message in channel.history(limit=limit + 1, oldest_first=False):
            count += 1
            if count > limit:
                result.truncated = True
                break
            messages.append(message)
    except discord.Forbidden as error:
        result.failed = True
        result.error = "у бота нет прав на чтение истории канала"
        logger.warning(
            f"transcript outcome=forbidden channel_id={channel_id} "
            f"error_type={type(error).__name__}"
        )
        return result
    except discord.HTTPException as error:
        result.failed = True
        result.error = f"Discord API вернул ошибку ({type(error).__name__})"
        logger.warning(
            f"transcript outcome=http_error channel_id={channel_id} "
            f"error_type={type(error).__name__}"
        )
        return result

    result.message_count = min(count, limit)
    if result.message_count == 0:
        return result

    body = [line for message in reversed(messages) for line in _format_message(message)]
    text = "\n".join(_header(channel, result.message_count, result.truncated) + body)
    data = text.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        result.size_limited = True
        data = data[:MAX_FILE_BYTES]
        data += "\n[файл обрезан по лимиту размера вложения Discord]".encode()

    result.files = [discord.File(io.BytesIO(data), filename=f"ticket-{channel_id}.txt")]
    logger.info(
        f"transcript outcome=ok channel_id={channel_id} messages={result.message_count} "
        f"truncated={result.truncated} size_limited={result.size_limited}"
    )
    return result


async def build_transcript_file(
    channel: Any, limit: int = MAX_MESSAGES
) -> list[discord.File] | None:
    """Возвращает файлы транскрипта или None, если сохранять нечего."""
    transcript = await build_transcript(channel, limit)
    return transcript.files or None
