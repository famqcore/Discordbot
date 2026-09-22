"""Автоответ на упоминание AFK-участника.

По спецификации активность пользователя НЕ снимает его AFK: статус
держится, пока не истечёт время (``afk/tasks.py``), либо пока его не снимет
модератор (``!afk_remove``) или сам участник кнопкой «Отменить AFK».
Обработчика ``on_voice_state_update`` здесь намеренно нет.

Защита от burst-нагрузки (issue #10):

- упоминания дедуплицируются, бот и автор сообщения исключаются;
- проверяется не больше ``AFK_MAX_MENTIONS_PER_MESSAGE`` участников,
  и все статусы читаются одним запросом к БД;
- на несколько AFK-участников формируется один компактный ответ, то есть
  одно обращение к Discord API вместо N;
- на канал действует окно ``AFK_REPLY_CHANNEL_LIMIT`` ответов;
- кулдаун резервируется атомарно до отправки и освобождается, если
  отправка не удалась — ошибка API не считается успешной доставкой;
- пропуски логируются агрегированно, без текста сообщений и имён.

Обработчик зарегистрирован как ``Cog.listener``, поэтому не перетирает
другие ``on_message`` и не отменяет ``process_commands`` (issue #21).
"""

from __future__ import annotations

import discord
from discord.ext import commands

import config
from utils.errors import log_event
from utils.logger import logger
from utils.mentions import escape_user_text, mentions_for
from utils.ratelimit import allow_within_window

from .models import (
    cancel_reply,
    check_and_reply,
    format_duration,
    get_afk_users,
    session_duration,
)

MAX_REPLY_LENGTH = 1900


def collect_mention_ids(message: discord.Message) -> list[int]:
    """Уникальные ID упомянутых участников без автора и ботов, с лимитом."""
    seen: list[int] = []
    for entity in getattr(message, "mentions", []) or []:
        user_id = getattr(entity, "id", None)
        if not isinstance(user_id, int):
            continue
        if getattr(entity, "bot", False):
            continue
        if user_id == getattr(message.author, "id", None):
            continue
        if user_id in seen:
            continue
        seen.append(user_id)
        if len(seen) >= config.AFK_MAX_MENTIONS_PER_MESSAGE:
            break
    return seen


def build_reply(entries: list[tuple[discord.abc.User, dict]]) -> str:
    """Один компактный ответ на всех AFK-участников сообщения."""
    lines = []
    for member, row in entries:
        reason = escape_user_text(row.get("afk_reason") or "Отошёл")
        duration = format_duration(session_duration(row))
        lines.append(
            config.AFK_AUTO_REPLY.format(
                mention=member.mention,
                reason=reason,
                duration=duration,
            )
        )
    text = "\n\n".join(lines)
    return text[:MAX_REPLY_LENGTH]


class AfkEventsCog(commands.Cog):
    """Слушатель сообщений: автоответы про AFK."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        # команды (!afk_check @user) не должны триггерить автоответ про AFK
        if message.content.startswith(config.CMD_PREFIX):
            return

        mention_ids = collect_mention_ids(message)
        if not mention_ids:
            return

        guild_id = message.guild.id
        afk_rows = get_afk_users(guild_id, mention_ids)
        if not afk_rows:
            return

        channel_id = getattr(message.channel, "id", 0)
        if not allow_within_window(
            ("afk_reply_channel", guild_id, channel_id),
            config.AFK_REPLY_CHANNEL_LIMIT,
            config.AFK_REPLY_CHANNEL_WINDOW_SECONDS,
        ):
            log_event(
                "afk.autoreply",
                outcome="channel_rate_limited",
                guild_id=guild_id,
                channel_id=channel_id,
                mentions=len(afk_rows),
            )
            return

        author_id = message.author.id
        entries: list[tuple[discord.abc.User, dict]] = []
        reserved: list[int] = []
        skipped = 0

        by_id = {entity.id: entity for entity in message.mentions}
        for user_id, row in afk_rows.items():
            if not check_and_reply(author_id, user_id, guild_id):
                skipped += 1
                continue
            member = by_id.get(user_id)
            if member is None:
                cancel_reply(author_id, user_id, guild_id)
                continue
            entries.append((member, row))
            reserved.append(user_id)

        if skipped:
            log_event(
                "afk.autoreply",
                outcome="cooldown_skipped",
                guild_id=guild_id,
                channel_id=channel_id,
                skipped=skipped,
            )

        if not entries:
            return

        try:
            await message.channel.send(
                build_reply(entries),
                delete_after=config.AFK_REPLY_DELETE_AFTER_SECONDS,
                allowed_mentions=mentions_for(users=[member for member, _ in entries]),
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            # отправка не состоялась: резерв кулдауна освобождаем,
            # иначе участник останется без автоответа до конца окна
            for user_id in reserved:
                cancel_reply(author_id, user_id, guild_id)
            logger.warning(
                f"afk.autoreply outcome=send_failed error_type={type(error).__name__} "
                f"guild_id={guild_id} channel_id={channel_id} recipients={len(entries)}"
            )
            return

        log_event(
            "afk.autoreply",
            guild_id=guild_id,
            channel_id=channel_id,
            recipients=len(entries),
        )


async def setup_afk_events(bot: commands.Bot) -> AfkEventsCog:
    """Регистрирует слушателя как Cog (безопасно для reload и других listeners)."""
    existing = bot.get_cog("AfkEventsCog")
    if existing is not None:
        return existing
    cog = AfkEventsCog(bot)
    await bot.add_cog(cog)
    return cog
