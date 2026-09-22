"""Поиск объектов сервера: сначала по ID из .env, имя — только запасной вариант.

Все каналы/роли настраиваются по ID (см. .env.example): переименование
объектов на сервере больше не ломает бота. Поиск по имени оставлен как
фолбэк, чтобы бот работал и без заполненных ID.
"""

import discord

from utils.logger import logger


def get_role(guild: discord.Guild, role_id: int | None, name: str | None = None):
    """Роль по ID (приоритетно) или по имени (фолбэк)."""
    if role_id:
        role = guild.get_role(role_id)
        if role is not None:
            return role
        logger.warning(f"Роль с ID {role_id} не найдена, ищу по имени «{name}»")
    if name:
        role = discord.utils.get(guild.roles, name=name)
        if role is None:
            logger.warning(f"Роль «{name}» не найдена на сервере {getattr(guild, 'name', guild)}")
        return role
    return None


def get_category(guild: discord.Guild, category_id: int | None, name: str):
    """Категория по ID (приоритетно) или по имени (фолбэк)."""
    if category_id:
        category = guild.get_channel(category_id)
        if isinstance(category, discord.CategoryChannel):
            return category
        logger.warning(f"Категория с ID {category_id} не найдена, ищу по имени «{name}»")
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        logger.warning(f"Категория «{name}» не найдена на сервере {getattr(guild, 'name', guild)}")
    return category


def get_voice_channel(guild: discord.Guild, channel_id: int | None, name: str):
    """Голосовой канал по ID (приоритетно) или по имени (фолбэк)."""
    if channel_id:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.VoiceChannel):
            return channel
        logger.warning(f"Голосовой канал с ID {channel_id} не найден, ищу по имени «{name}»")
    return discord.utils.get(guild.voice_channels, name=name)
