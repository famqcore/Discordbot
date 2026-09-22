"""Фоновая задача AFK: авто-снятие статуса, когда время возврата истекло.

По спецификации статус AFK не зависит от активности пользователя
(сообщения, голосовые каналы его не снимают). AFK снимается только:
  - по истечении указанного времени (эта задача),
  - модератором командой !afk_remove,
  - самим пользователем кнопкой «Отменить AFK» в меню !afk.
"""

from datetime import datetime, timedelta

import discord
from discord.ext import tasks

import config
from database.afk_db import cleanup_cooldowns, get_expired_afk
from utils.logcenter import LOG_KEY_AFK, send_to_log
from utils.logger import logger

from .models import format_duration, remove_afk, remove_afk_nickname

_started = False


async def expire_afk_once(bot) -> int:
    """Снимает все истёкшие AFK один раз. Возвращает число снятых."""
    now = datetime.now()
    now_iso = now.isoformat()
    expired_total = 0

    # заодно чистим старые записи кулдауна автоответа, чтобы таблица не росла
    try:
        cutoff = (now - timedelta(days=1)).isoformat()
        removed = cleanup_cooldowns(cutoff)
        if removed:
            logger.debug(f"AFK-expiry: удалено устаревших кулдаунов: {removed}")
    except Exception as e:
        logger.error(f"AFK-expiry: не удалось почистить кулдауны: {e}")

    for guild in bot.guilds:
        try:
            rows = get_expired_afk(guild.id, now_iso)
        except Exception as e:
            logger.error(
                f"AFK-expiry: ошибка чтения БД для сервера {getattr(guild, 'id', guild)}: {e}"
            )
            continue

        for row in rows:
            user_id = row["user_id"]
            try:
                duration = remove_afk(user_id, guild.id)
            except Exception as e:
                logger.error(f"AFK-expiry: не удалось снять AFK с {user_id}: {e}")
                continue
            if duration is None:
                continue

            expired_total += 1
            original_nick = (
                row.get("original_nick") if isinstance(row, dict) else row["original_nick"]
            )
            member = guild.get_member(user_id)
            if member is not None:
                try:
                    await remove_afk_nickname(member, original_nick)
                except Exception as e:
                    logger.warning(f"AFK-expiry: не смог убрать префикс ника у {user_id}: {e}")
                try:
                    await member.send(config.AFK_EXPIRED_DM.format(guild=guild.name))
                except Exception:
                    pass  # личка закрыта — не критично

            embed = discord.Embed(
                title=config.AFK_LOG_EXPIRED_TITLE,
                color=discord.Color.light_grey(),
            )
            embed.add_field(name="Пользователь", value=f"<@{user_id}>", inline=True)
            embed.add_field(name="Отсутствовал", value=format_duration(duration), inline=True)
            await send_to_log(guild, LOG_KEY_AFK, embed=embed)

    return expired_total


def start_expiry_loop(bot):
    """Запускает периодическую проверку истёкших AFK. Повторный вызов игнорируется."""
    global _started
    if _started:
        logger.warning("AFK-expiry: цикл уже запущен, пропускаю повторный запуск")
        return

    @tasks.loop(seconds=config.AFK_EXPIRY_CHECK_SECONDS)
    async def _expiry_loop():
        try:
            count = await expire_afk_once(bot)
            if count:
                logger.info(f"AFK-expiry: снято истёкших AFK: {count}")
        except Exception as e:
            logger.error(f"AFK-expiry: ошибка цикла: {e}")

    @_expiry_loop.before_loop
    async def _before_expiry_loop():
        await bot.wait_until_ready()

    _expiry_loop.start()
    _started = True
    logger.info(
        f"AFK-expiry: запущен цикл авто-снятия, интервал {config.AFK_EXPIRY_CHECK_SECONDS} сек"
    )
