"""Фоновая ретенция: удаление заявок старше срока хранения.

Политика зафиксирована в docs/privacy.md. За один проход удаляются:

- записи тикетов из БД (конечные — по дате закрытия, заброшенные активные
  — по дате создания);
- каналы таких активных тикетов, если они ещё существуют;
- привязанные сообщения лог-центра вместе с транскриптами-вложениями.

Агрегированная статистика (таблица stats) не удаляется: персональных
данных в ней нет.

Технические логи и бэкапы SQLite вне задачи: их ротацией управляет
инфраструктура (docs/deployment.md).

Цикл живёт на экземпляре Cog и отменяется в ``cog_unload``.
"""

from __future__ import annotations

import sqlite3

import discord
from discord.ext import commands, tasks

import config
from database import tickets_db
from database.schema import ACTIVE_STATUSES
from utils import clock
from utils.errors import guard_background, log_event
from utils.logcenter import LOG_KEY_AUDIT, delete_log_messages, send_to_log
from utils.logger import logger


async def _purge_ticket(bot, ticket) -> bool:
    """Удаляет один тикет и связанный контент. True — запись БД удалена."""
    guild = bot.get_guild(ticket["guild_id"])
    try:
        if guild is not None:
            if ticket["status"] in ACTIVE_STATUSES:
                channel = guild.get_channel(ticket["channel_id"])
                if channel is not None:
                    await channel.delete(reason="Ретенция: заявка старше срока хранения")
            refs = tickets_db.parse_log_message_refs(ticket["log_message_ids"])
            if refs:
                await delete_log_messages(guild, refs)
        return tickets_db.delete_ticket_by_id(ticket["id"])
    except (discord.Forbidden, discord.HTTPException, sqlite3.Error) as error:
        logger.exception(
            f"retention outcome=purge_failed ticket_id={ticket['id']} "
            f"error_type={type(error).__name__}"
        )
        return False


async def purge_expired_once(bot) -> int:
    """Один проход ретенции по всем серверам. Возвращает число удалённых записей."""
    cutoff = clock.to_db(clock.shift(clock.utcnow(), days=-config.TICKET_RETENTION_DAYS))
    purged_per_guild: dict[int, int] = {}

    for ticket in tickets_db.get_retention_expired(cutoff):
        if await _purge_ticket(bot, ticket):
            purged_per_guild[ticket["guild_id"]] = purged_per_guild.get(ticket["guild_id"], 0) + 1

    purged_total = sum(purged_per_guild.values())
    if not purged_total:
        return 0

    for guild_id, count in purged_per_guild.items():
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        embed = discord.Embed(title=config.RETENTION_AUDIT_TITLE, color=discord.Color.dark_grey())
        embed.add_field(name="Удалено заявок", value=str(count), inline=True)
        embed.add_field(
            name="Срок хранения", value=f"{config.TICKET_RETENTION_DAYS} дн", inline=True
        )
        await send_to_log(guild, LOG_KEY_AUDIT, embed=embed)

    log_event("retention", purged=purged_total, retention_days=config.TICKET_RETENTION_DAYS)
    return purged_total


class TicketRetentionCog(commands.Cog):
    """Владелец периодической задачи ретенции."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.retention_loop.change_interval(seconds=config.RETENTION_CHECK_SECONDS)
        self.retention_loop.start()
        logger.info(
            f"retention outcome=started retention_days={config.TICKET_RETENTION_DAYS} "
            f"interval={config.RETENTION_CHECK_SECONDS}s"
        )

    def cog_unload(self) -> None:
        self.retention_loop.cancel()
        logger.info("retention outcome=stopped")

    @tasks.loop(seconds=86400)
    async def retention_loop(self) -> None:
        await guard_background(lambda: purge_expired_once(self.bot), event="retention.cycle")

    @retention_loop.before_loop
    async def before_retention_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup_retention_loop(bot: commands.Bot) -> TicketRetentionCog:
    existing = bot.get_cog("TicketRetentionCog")
    if existing is not None:
        return existing
    cog = TicketRetentionCog(bot)
    await bot.add_cog(cog)
    return cog
