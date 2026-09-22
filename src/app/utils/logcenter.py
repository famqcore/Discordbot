"""Лог-центр: один приватный канал, внутри — ветки по категориям логов.

Канал и ветки ищутся по ID из .env (приоритетно) или по имени (фолбэк),
а при отсутствии — создаются автоматически. Лог-канал создаётся приватным:
закрыт для @everyone, открыт боту и стафф-ролям.

Гарантия устойчивости: send_to_log никогда не бросает исключений наружу —
логирование не должно ронять основную логику бота.
"""

import discord

import config
from utils.logger import logger

# Ключи веток (совпадают с config.LOG_KEY_*)
LOG_KEY_TICKETS = config.LOG_KEY_TICKETS
LOG_KEY_DECISIONS = config.LOG_KEY_DECISIONS
LOG_KEY_AFK = config.LOG_KEY_AFK
LOG_KEY_CALLS = config.LOG_KEY_CALLS
LOG_KEY_STATS = config.LOG_KEY_STATS
LOG_KEY_ERRORS = config.LOG_KEY_ERRORS

MAX_AUTO_ARCHIVE = 10080  # неделя — максимум у Discord


def _private_overwrites(guild):
    """Права для лог-канала: приватный, доступен боту и стафф-ролям."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


async def _resolve_log_channel(guild):
    """Канал лог-центра: по ID → по имени → автосоздание (приватный)."""
    if config.LOG_CHANNEL_ID:
        channel = guild.get_channel(config.LOG_CHANNEL_ID)
        if channel is None:
            try:
                channel = await guild.fetch_channel(config.LOG_CHANNEL_ID)
            except Exception:
                channel = None
        if channel is not None:
            return channel
        logger.warning(f"Лог-канал с ID {config.LOG_CHANNEL_ID} не найден, ищу по имени")

    channel = discord.utils.get(guild.text_channels, name=config.LOG_CHANNEL_NAME)
    if channel is None:
        channel = await guild.create_text_channel(
            config.LOG_CHANNEL_NAME, overwrites=_private_overwrites(guild)
        )
        logger.info(f"Создал приватный лог-канал «{config.LOG_CHANNEL_NAME}»")
    return channel


async def _resolve_thread(guild, key: str):
    """Ветка для категории логов: по ID → по имени → автосоздание."""
    channel = await _resolve_log_channel(guild)

    thread_id = config.LOG_THREAD_IDS.get(key)
    name = config.LOG_THREAD_NAMES.get(key, key)

    if thread_id:
        thread = guild.get_thread(thread_id)
        if thread is None:
            try:
                thread = await guild.fetch_channel(thread_id)
            except Exception:
                thread = None
        if thread is not None:
            return thread
        logger.warning(f"Ветка логов «{key}» с ID {thread_id} не найдена, ищу по имени")

    thread = discord.utils.get(channel.threads, name=name)
    if thread is None:
        thread = await channel.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=MAX_AUTO_ARCHIVE,
        )
        logger.info(f"Создал ветку логов «{name}» в канале «{config.LOG_CHANNEL_NAME}»")
    return thread


async def send_to_log(guild, key: str, content: str | None = None, embed=None, files=None) -> bool:
    """Отправляет сообщение в ветку лог-центра.

    Возвращает True при успехе. Никогда не бросает исключений:
    при недоступной ветке шлёт в корень канала, при недоступном канале — False.
    """
    if guild is None:
        return False

    try:
        destination = await _resolve_thread(guild, key)
    except Exception as e:
        logger.warning(f"logcenter: ветка «{key}» недоступна ({e}), пробую сам канал")
        try:
            destination = await _resolve_log_channel(guild)
        except Exception as e2:
            logger.error(f"logcenter: лог-канал недоступен: {e2}")
            return False

    try:
        await destination.send(content=content, embed=embed, files=files)
        return True
    except Exception as e:
        logger.warning(f"logcenter: не удалось отправить лог «{key}»: {e}")
        return False
