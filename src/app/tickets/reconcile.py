"""Reconciliation: устранение orphan-состояний заявок.

Пара «запись в БД — канал Discord» может разойтись, если процесс упал
между шагами или Discord API отказал во время уборки. Периодическая
задача приводит состояние к согласованному:

- **активная заявка без канала.** Канал удалён вручную или уборкой после
  сбоя: запись переводится в `closed` с пометкой причины. Пользователь
  снова может подать заявку — уникальный индекс освобождается;
- **зависший `processing`.** Терминальное действие не дошло до финализации
  (рестарт, разрыв связи). Если канал жив — заявка возвращается в `open`
  и модератор повторяет действие; если канала нет — заявка закрывается;
- канал, для которого нет записи, не удаляется автоматически: бот не
  трогает каналы, которые могли быть созданы не им. Такие случаи
  попадают в лог для решения оператором.
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

import config
from database.schema import STATUS_CLOSED, STATUS_PROCESSING
from database.tickets_db import (
    async_get_active_tickets,
    async_get_stale_processing,
    async_release_transition,
    async_update_ticket_status,
)
from utils import clock
from utils.errors import guard_background, log_event
from utils.logger import logger

CLOSE_REASON_NO_CHANNEL = "closed by reconciliation: канал тикета не существует"


async def _fetch_channel(guild, channel_id: int):
    channel = guild.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    except discord.HTTPException as error:
        # неизвестно, есть канал или нет — на этом проходе не трогаем
        logger.warning(
            f"ticket.reconcile outcome=fetch_failed error_type={type(error).__name__} "
            f"channel_id={channel_id}"
        )
        raise


async def reconcile_once(bot) -> dict[str, int]:
    """Один проход. Возвращает счётчики выполненных исправлений."""
    counters = {"closed_without_channel": 0, "released_processing": 0, "checked": 0}

    stale_cutoff = clock.to_db(
        clock.shift(clock.utcnow(), seconds=-config.TICKET_PROCESSING_TIMEOUT_SECONDS)
    )
    stale_ids = {row["channel_id"] for row in await async_get_stale_processing(stale_cutoff)}

    for ticket in await async_get_active_tickets():
        guild = bot.get_guild(ticket["guild_id"])
        if guild is None:
            continue  # сервер недоступен — состояние проверим позже
        counters["checked"] += 1

        channel_id = ticket["channel_id"]
        try:
            channel = await _fetch_channel(guild, channel_id)
        except discord.HTTPException:
            continue

        if channel is None:
            if await async_update_ticket_status(
                channel_id, STATUS_CLOSED, reason=CLOSE_REASON_NO_CHANNEL, guild_id=guild.id
            ):
                counters["closed_without_channel"] += 1
                log_event(
                    "ticket.reconcile",
                    outcome="closed_without_channel",
                    guild_id=guild.id,
                    channel_id=channel_id,
                    ticket_id=ticket["id"],
                )
            continue

        if ticket["status"] == STATUS_PROCESSING and channel_id in stale_ids:
            if await async_release_transition(channel_id):
                counters["released_processing"] += 1
                log_event(
                    "ticket.reconcile",
                    outcome="released_processing",
                    guild_id=guild.id,
                    channel_id=channel_id,
                    ticket_id=ticket["id"],
                )

    if counters["closed_without_channel"] or counters["released_processing"]:
        logger.info(
            f"ticket.reconcile outcome=ok checked={counters['checked']} "
            f"closed={counters['closed_without_channel']} "
            f"released={counters['released_processing']}"
        )
    return counters


class TicketReconcileCog(commands.Cog):
    """Владелец периодической reconciliation-задачи."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reconcile_loop.change_interval(seconds=config.TICKET_RECONCILE_CHECK_SECONDS)
        self.reconcile_loop.start()
        logger.info(
            f"ticket.reconcile outcome=started interval={config.TICKET_RECONCILE_CHECK_SECONDS}s"
        )

    def cog_unload(self) -> None:
        self.reconcile_loop.cancel()
        logger.info("ticket.reconcile outcome=stopped")

    @tasks.loop(seconds=600)
    async def reconcile_loop(self) -> None:
        await guard_background(lambda: reconcile_once(self.bot), event="ticket.reconcile.cycle")

    @reconcile_loop.before_loop
    async def before_reconcile_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup_reconcile_loop(bot: commands.Bot) -> TicketReconcileCog:
    existing = bot.get_cog("TicketReconcileCog")
    if existing is not None:
        return existing
    cog = TicketReconcileCog(bot)
    await bot.add_cog(cog)
    return cog
