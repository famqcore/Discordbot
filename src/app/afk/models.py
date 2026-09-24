"""Бизнес-логика AFK: сессии, ники, статистика.

Правила работы с ником:

- ``original_nick`` фиксируется один раз, при старте сессии, и переживает
  любое число обновлений причины/времени;
- восстановление возвращает ровно исходное значение, включая ``None``
  для участника, у которого server nickname не было;
- префикс распознаётся только в начале строки (``startswith``), поэтому
  собственный текст «[AFK]» в середине имени не считается служебным;
- бот трогает ник только если сам его и ставил (``nick_applied``): ник,
  изменённый участником во время AFK, остаётся за участником.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import discord

import config
from database import afk_db
from database.afk_db import AfkSetResult
from utils import clock
from utils.errors import log_event
from utils.logger import logger

AfkRecord = Mapping[str, Any]


def format_duration(seconds: int | float) -> str:
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


def set_afk(
    user_id: int,
    guild_id: int,
    reason: str,
    estimated_return: str | None = None,
    original_nick: str | None = None,
    nick_applied: bool = False,
) -> AfkSetResult:
    """Ставит AFK или обновляет параметры уже идущей сессии.

    Счётчик уходов увеличивается только у новой сессии; старт и исходный
    ник активной сессии не перезаписываются.
    """
    return afk_db.set_afk(
        user_id,
        guild_id,
        reason,
        afk_since=clock.to_db(),
        estimated_return=estimated_return,
        original_nick=original_nick,
        nick_applied=nick_applied,
    )


def remove_afk(user_id: int, guild_id: int) -> int | None:
    """Снимает AFK. Возвращает длительность сессии или None.

    None означает, что записи уже не было: конкурирующее снятие (кнопка,
    команда модератора, expiry loop) победило и статистику обновило оно.
    """
    snapshot = afk_db.take_afk(user_id, guild_id)
    if snapshot is None:
        return None
    return snapshot["duration_seconds"]


def take_afk_session(user_id: int, guild_id: int) -> dict[str, Any] | None:
    """Снимает AFK и возвращает полный снимок сессии победившей операции."""
    return afk_db.take_afk(user_id, guild_id)


def get_afk_user(user_id: int, guild_id: int) -> dict[str, Any] | None:
    row = afk_db.get_afk_user(user_id, guild_id)
    return dict(row) if row else None


def get_all_afk(guild_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in afk_db.get_all_afk(guild_id)]


def get_afk_users(guild_id: int, user_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """AFK-записи набора участников одним запросом: {user_id: row}."""
    return {row["user_id"]: dict(row) for row in afk_db.get_afk_users(guild_id, user_ids)}


def check_and_reply(mentioner_id: int, afk_user_id: int, guild_id: int = 0) -> bool:
    """Резервирует право на автоответ (атомарно). False — окно не истекло."""
    return afk_db.reserve_cooldown(
        mentioner_id,
        afk_user_id,
        config.AFK_COOLDOWN_SECONDS,
        guild_id=guild_id,
    )


def cancel_reply(mentioner_id: int, afk_user_id: int, guild_id: int = 0) -> None:
    """Возвращает резерв: отправка автоответа не состоялась."""
    afk_db.release_cooldown(mentioner_id, afk_user_id, guild_id=guild_id)


def get_user_stats(user_id: int, guild_id: int | None = None) -> dict[str, Any] | None:
    row = afk_db.get_user_stats(user_id, guild_id)
    return dict(row) if row else None


def session_duration(row: AfkRecord | None) -> int:
    """Длительность текущей сессии по записи AFK."""
    if not row:
        return 0
    return clock.seconds_between(clock.parse_db(row.get("afk_since")))


# ---------------------------------------------------------------------------
# Ники
# ---------------------------------------------------------------------------


def has_afk_prefix(nick: str | None) -> bool:
    """Префикс считается служебным только в начале строки."""
    return bool(nick) and nick.startswith(config.AFK_NICK_PREFIX)


def build_afk_nickname(member: discord.Member) -> str:
    base = member.display_name or ""
    limit = config.DISCORD_NICK_MAX_LENGTH - len(config.AFK_NICK_PREFIX)
    return f"{config.AFK_NICK_PREFIX}{base[:limit]}"


async def add_afk_nickname(member: discord.Member) -> bool:
    """Ставит префикс. True — ник принадлежит боту (или уже был с префиксом)."""
    if has_afk_prefix(member.nick):
        return True
    try:
        await member.edit(nick=build_afk_nickname(member))
    except discord.Forbidden:
        log_event(
            "afk.nickname_set",
            outcome="forbidden",
            level=30,
            guild_id=getattr(getattr(member, "guild", None), "id", None),
            user_id=getattr(member, "id", None),
        )
        return False
    except discord.HTTPException as error:
        logger.warning(
            f"afk.nickname_set outcome=error error_type={type(error).__name__} "
            f"user_id={getattr(member, 'id', None)}"
        )
        return False
    return True


async def remove_afk_nickname(
    member: discord.Member,
    original_nick: str | None = None,
    nick_applied: bool = True,
) -> bool:
    """Возвращает исходный ник участника.

    ``original_nick=None`` означает, что до AFK server nickname не было —
    восстанавливается именно ``None``, а не копия имени аккаунта. Если
    префикса нет (участник переименовался сам) либо ник ставил не бот,
    чужое имя не трогается.
    """
    current = member.nick
    if not has_afk_prefix(current):
        return True
    if not nick_applied:
        # префикс поставил не бот: политика — не перетирать чужое решение
        log_event(
            "afk.nickname_restore",
            outcome="skipped_not_owned",
            guild_id=getattr(getattr(member, "guild", None), "id", None),
            user_id=getattr(member, "id", None),
        )
        return True

    target = original_nick[: config.DISCORD_NICK_MAX_LENGTH] if original_nick else None
    try:
        await member.edit(nick=target)
    except discord.Forbidden:
        log_event(
            "afk.nickname_restore",
            outcome="forbidden",
            level=30,
            guild_id=getattr(getattr(member, "guild", None), "id", None),
            user_id=getattr(member, "id", None),
        )
        return False
    except discord.HTTPException as error:
        logger.warning(
            f"afk.nickname_restore outcome=error error_type={type(error).__name__} "
            f"user_id={getattr(member, 'id', None)}"
        )
        return False
    return True


def mark_nick_applied(user_id: int, guild_id: int, applied: bool = True) -> None:
    afk_db.mark_nick_applied(user_id, guild_id, applied)
