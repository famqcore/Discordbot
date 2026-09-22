"""Сохранение истории тикета в текстовый файл перед удалением канала."""

import io

import discord

from utils.logger import logger


async def build_transcript_file(channel, limit: int = 200):
    """История канала текстом для лога. Возвращает [discord.File] или None."""
    try:
        lines = []
        async for msg in channel.history(limit=limit, oldest_first=True):
            stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{stamp}] {msg.author}: {msg.clean_content}")
        if not lines:
            return None
        data = "\n".join(lines).encode("utf-8")
        return [discord.File(io.BytesIO(data), filename=f"ticket-{channel.id}.txt")]
    except Exception as e:
        logger.warning(f"Не удалось собрать транскрипт канала {getattr(channel, 'id', '?')}: {e}")
        return None
