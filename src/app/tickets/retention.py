"""Фоновая ретенция: удаление заявок старше срока хранения (TICKET_RETENTION_DAYS).

Политика зафиксирована в docs/privacy.md. За один проход удаляются:

- записи тикетов из БД (закрытые — по дате закрытия, заброшенные открытые
  — по дате создания);
- каналы таких открытых тикетов, если они ещё существуют;
- привязанные сообщения лог-центра вместе с транскриптами-вложениями.

Агрегированная статистика (таблица stats) не удаляется: персональных
данных в ней нет.

Технические логи и бэкапы SQLite вне задачи: их ротацией управляет
инфраструктура (docs/deployment.md).
"""

from datetime import datetime, timedelta

import discord
from discord.ext import tasks

import config
from database import tickets_db
from utils.logcenter import LOG_KEY_AUDIT, delete_log_messages, send_to_log
from utils.logger import logger

_started = False


async def _purge_ticket(bot, ticket) -> bool:
    """Удаляет один тикет и связанный контент. True — запись БД удалена."""
    guild = bot.get_guild(ticket["guild_id"])
    try:
        if guild is not None:
            if ticket["status"] == "open":
                channel = guild.get_channel(ticket["channel_id"])
                if channel is not None:
                    await channel.delete(reason="Ретенция: заявка старше срока хранения")
            refs = tickets_db.parse_log_message_refs(ticket["log_message_ids"])
            if refs:
                await delete_log_messages(guild, refs)
        return tickets_db.delete_ticket_by_id(ticket["id"])
    except Exception as e:
        logger.error(f"retention: не удалось удалить тикет #{ticket['id']}: {e}")
        return False


async def purge_expired_once(bot) -> int:
    """Один проход ретенции по всем серверам. Возвращает число удалённых записей."""
    cutoff = (datetime.now() - timedelta(days=config.TICKET_RETENTION_DAYS)).isoformat()
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
    logger.info(f"retention: удалено заявок старше срока хранения: {purged_total}")
    return purged_total


def start_retention_loop(bot):
    """Запускает периодическую ретенцию. Повторный вызов игнорируется."""
    global _started
    if _started:
        logger.warning("retention: цикл уже запущен, пропускаю повторный запуск")
        return

    @tasks.loop(seconds=config.RETENTION_CHECK_SECONDS)
    async def _retention_loop():
        try:
            await purge_expired_once(bot)
        except Exception as e:
            logger.error(f"retention: ошибка цикла: {e}")

    @_retention_loop.before_loop
    async def _before_retention_loop():
        await bot.wait_until_ready()

    _retention_loop.start()
    _started = True
    logger.info(
        f"retention: запущен цикл очистки, срок хранения {config.TICKET_RETENTION_DAYS} дн, "
        f"интервал {config.RETENTION_CHECK_SECONDS} сек"
    )
