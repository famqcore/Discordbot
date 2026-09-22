from datetime import datetime

import discord

import config
from database.afk_db import (
    check_cooldown as db_check_cooldown,
    get_afk_user as db_get_afk_user,
    get_all_afk as db_get_all_afk,
    get_user_stats as db_get_user_stats,
    remove_afk as db_remove_afk,
    set_afk as db_set_afk,
    set_cooldown as db_set_cooldown,
    update_stats_on_remove,
    update_stats_on_set,
)


def format_duration(seconds):
    minutes, sec = divmod(max(int(seconds), 0), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if sec or not parts:
        parts.append(f"{sec} сек")
    return " ".join(parts)


def set_afk(user_id, guild_id, reason, estimated_return=None, original_nick=None):
    now = datetime.now().isoformat()
    was_afk = db_get_afk_user(user_id, guild_id) is not None
    db_set_afk(user_id, guild_id, reason, now, estimated_return, original_nick)
    # обновление причины у уже стоящего AFK — не новый уход, статистику не плюсуем
    if not was_afk:
        update_stats_on_set(user_id, guild_id)


def remove_afk(user_id, guild_id):
    row = db_get_afk_user(user_id, guild_id)
    if not row:
        return None
    afk_since = datetime.fromisoformat(row["afk_since"])
    duration = int((datetime.now() - afk_since).total_seconds())
    db_remove_afk(user_id, guild_id)
    update_stats_on_remove(user_id, duration, guild_id)
    return duration


def get_afk_user(user_id, guild_id):
    row = db_get_afk_user(user_id, guild_id)
    return dict(row) if row else None


def get_all_afk(guild_id):
    return [dict(row) for row in db_get_all_afk(guild_id)]


def check_and_reply(mentioner_id, afk_user_id, guild_id=0):
    if db_check_cooldown(
        mentioner_id,
        afk_user_id,
        config.AFK_COOLDOWN_SECONDS,
        guild_id=guild_id,
    ):
        db_set_cooldown(mentioner_id, afk_user_id, guild_id)
        return True
    return False


def get_user_stats(user_id, guild_id=None):
    row = db_get_user_stats(user_id, guild_id)
    return dict(row) if row else None


async def add_afk_nickname(member):
    if member.nick and config.AFK_NICK_PREFIX in member.nick:
        return True
    new_nick = f"{config.AFK_NICK_PREFIX}{member.display_name}"[:32]
    try:
        await member.edit(nick=new_nick)
        return True
    except discord.Forbidden:
        return False


async def remove_afk_nickname(member, original_nick=None):
    if not member.nick or config.AFK_NICK_PREFIX not in member.nick:
        return True
    if original_nick:
        new_nick = original_nick[:32]
    else:
        new_nick = member.nick.replace(config.AFK_NICK_PREFIX, "", 1).strip()[:32]
        # если до AFK своего ника не было — возвращаем None, а не копию имени
        if not new_nick or new_nick == member.display_name:
            new_nick = None
    try:
        await member.edit(nick=new_nick)
        return True
    except discord.Forbidden:
        return False
