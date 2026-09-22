"""Фоновое снятие истёкших AFK.

Спецификация: AFK снимается по времени возврата, указанному участником.
Возвращение в чат или в голосовой канал статус не снимает — это делает
только этот цикл, модератор (``!afk_remove``) или сам участник кнопкой.

Lifecycle (issue #21): цикл живёт на экземпляре Cog, а не в module-global.
Задача стартует после ``wait_until_ready`` и отменяется в ``cog_unload``,
поэтому reload расширения и несколько bot instance не оставляют висящих
задач и не плодят дубли.
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

import config
from database.afk_db import cleanup_cooldowns, get_expired_afk
from utils import clock, ratelimit
from utils.errors import guard_background, log_event
from utils.logcenter import LOG_KEY_AFK, send_to_log
from utils.logger import logger

from .models import format_duration, remove_afk_nickname, take_afk_session

COOLDOWN_RETENTION_DAYS = 1


async def expire_afk_once(bot) -> int:
    """Снимает все истёкшие AFK один раз. Возвращает число снятых."""
    now = clock.utcnow()
    now_iso = clock.to_db(now)
    expired_total = 0

    # заодно чистим старые записи кулдауна автоответа и память лимитера
    await guard_background(
        lambda: _cleanup_cooldowns(now),
        event="afk.expiry.cleanup",
    )

    for guild in bot.guilds:
        guild_id = getattr(guild, "id", None)
        try:
            rows = get_expired_afk(guild_id, now_iso)
        except Exception as error:  # noqa: BLE001 - цикл не должен падать из-за одного сервера
            logger.exception(
                f"afk.expiry outcome=db_error error_type={type(error).__name__} "
                f"guild_id={guild_id}"
            )
            continue

        for row in rows:
            user_id = row["user_id"]
            session = await guard_background(
                lambda uid=user_id, gid=guild_id: _take_session(uid, gid),
                event="afk.expiry.take",
                guild_id=guild_id,
                user_id=user_id,
            )
            if not session:
                continue

            expired_total += 1
            duration = session["duration_seconds"]
            member = guild.get_member(user_id)
            if member is not None:
                await _restore_member(member, session, guild_id, user_id)

            embed = discord.Embed(
                title=config.AFK_LOG_EXPIRED_TITLE,
                color=discord.Color.light_grey(),
            )
            embed.add_field(name="Пользователь", value=f"<@{user_id}>", inline=True)
            embed.add_field(name="Отсутствовал", value=format_duration(duration), inline=True)
            await send_to_log(guild, LOG_KEY_AFK, embed=embed)

            log_event(
                "afk.removed",
                guild_id=guild_id,
                user_id=user_id,
                actor="expiry",
                duration_seconds=duration,
            )

    return expired_total


async def _cleanup_cooldowns(now) -> None:
    cutoff = clock.to_db(clock.shift(now, days=-COOLDOWN_RETENTION_DAYS))
    removed = cleanup_cooldowns(cutoff)
    limiter_removed = ratelimit.cleanup()
    if removed or limiter_removed:
        logger.debug(
            f"afk.expiry.cleanup outcome=ok cooldowns={removed} "
            f"ratelimit_entries={limiter_removed} ratelimit_size={ratelimit.stats()['size']}"
        )


def _take_session(user_id: int, guild_id: int) -> dict | None:
    return take_afk_session(user_id, guild_id)


async def _restore_member(member, session: dict, guild_id, user_id) -> None:
    try:
        await remove_afk_nickname(
            member,
            session.get("original_nick"),
            nick_applied=bool(session.get("nick_applied")),
        )
    except discord.HTTPException as error:
        logger.warning(
            f"afk.expiry outcome=nick_restore_failed error_type={type(error).__name__} "
            f"guild_id={guild_id} user_id={user_id}"
        )

    try:
        await member.send(config.AFK_EXPIRED_DM.format(guild=member.guild.name))
    except discord.Forbidden:
        pass  # личка закрыта — ожидаемо, не ошибка
    except discord.HTTPException as error:
        logger.warning(
            f"afk.expiry outcome=dm_failed error_type={type(error).__name__} user_id={user_id}"
        )


class AfkExpiryCog(commands.Cog):
    """Владелец периодической задачи авто-снятия AFK."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.expiry_loop.change_interval(seconds=config.AFK_EXPIRY_CHECK_SECONDS)
        self.expiry_loop.start()
        logger.info(f"afk.expiry outcome=started interval={config.AFK_EXPIRY_CHECK_SECONDS}s")

    def cog_unload(self) -> None:
        self.expiry_loop.cancel()
        logger.info("afk.expiry outcome=stopped")

    @tasks.loop(seconds=60)
    async def expiry_loop(self) -> None:
        count = await guard_background(
            lambda: expire_afk_once(self.bot),
            event="afk.expiry.cycle",
        )
        if count:
            logger.info(f"afk.expiry outcome=ok expired={count}")

    @expiry_loop.before_loop
    async def before_expiry_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup_expiry_loop(bot: commands.Bot) -> AfkExpiryCog:
    """Регистрирует Cog с циклом; повторный вызов возвращает существующий."""
    existing = bot.get_cog("AfkExpiryCog")
    if existing is not None:
        return existing
    cog = AfkExpiryCog(bot)
    await bot.add_cog(cog)
    return cog
