"""Поиск объектов сервера строго по ID из .env.

Имена ролей в Discord не уникальны, поэтому поиск по имени никогда не
заменяет заданный ID: одноимённая роль не должна получить доступ к тикетам
или право модерации. Объект без ID безопасно пропускается (None), а объект
с заданным, но не найденным ID — повод остановить запуск (см. utils/startup.py).

Поиск по имени остаётся только в небезопасном режиме разработки
(config.ALLOW_NAME_FALLBACK=true) — явный opt-in для локальных стендов.
"""

import discord

import config
from utils.logger import logger


def _by_name_allowed() -> bool:
    return bool(getattr(config, "ALLOW_NAME_FALLBACK", False))


def _report_missing(what: str, object_id: int, guild: discord.Guild) -> None:
    if _by_name_allowed():
        logger.warning(f"{what} с ID {object_id} не найден, ищу по имени")
    else:
        logger.warning(
            f"{what} с ID {object_id} не найден на сервере "
            f"{getattr(guild, 'id', guild)} — проверьте .env (поиск по имени отключён)"
        )


def get_role(
    guild: discord.Guild, role_id: int | None, name: str | None = None
) -> discord.Role | None:
    """Роль по ID. Поиск по имени — только в режиме разработки."""
    if role_id:
        role = guild.get_role(role_id)
        if role is not None:
            return role
        _report_missing("Роль", role_id, guild)
        if not _by_name_allowed():
            return None
    if name and _by_name_allowed():
        role = discord.utils.get(guild.roles, name=name)
        if role is None:
            logger.warning(f"Роль «{name}» не найдена на сервере {getattr(guild, 'name', guild)}")
        return role
    return None


def get_category(
    guild: discord.Guild, category_id: int | None, name: str
) -> discord.CategoryChannel | None:
    """Категория по ID. Поиск по имени — только в режиме разработки."""
    if category_id:
        category = guild.get_channel(category_id)
        if isinstance(category, discord.CategoryChannel):
            return category
        _report_missing("Категория", category_id, guild)
        if not _by_name_allowed():
            return None
    if _by_name_allowed():
        category = discord.utils.get(guild.categories, name=name)
        if category is None:
            logger.warning(
                f"Категория «{name}» не найдена на сервере {getattr(guild, 'name', guild)}"
            )
        return category
    return None


def get_voice_channel(
    guild: discord.Guild, channel_id: int | None, name: str
) -> discord.VoiceChannel | None:
    """Голосовой канал по ID. Поиск по имени — только в режиме разработки."""
    if channel_id:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.VoiceChannel):
            return channel
        _report_missing("Голосовой канал", channel_id, guild)
        if not _by_name_allowed():
            return None
    if _by_name_allowed():
        channel = discord.utils.get(guild.voice_channels, name=name)
        if channel is None:
            logger.warning(
                f"Голосовой канал «{name}» не найден на сервере {getattr(guild, 'name', guild)}"
            )
        return channel
    return None
