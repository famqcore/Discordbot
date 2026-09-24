"""Удаление персональных данных пользователя (команда !delete_user_data).

Объём операции согласован с политикой (docs/privacy.md) и текстом
подтверждения config.PRIVACY_DELETE_CONFIRM:

- открытый тикет удаляется вместе с каналом и перепиской;
- записи тикетов анонимизируются (ответы формы, имя, ID заявителя);
- связанные сообщения лог-центра (включая транскрипты-вложения) удаляются;
- AFK-статус, AFK-статистика и кулдауны удаляются, ник восстанавливается;
- агрегированная статистика решений остаётся: персональных данных в ней нет;
- действие администратора фиксируется в аудит-ветке лог-центра минимально
  необходимой записью (ID и счётчики, без самих персональных данных);
- копии в резервных бэкапах исчезают по мере их ротации.
"""

import discord

import config
from afk.models import remove_afk_nickname
from database import afk_db, tickets_db
from database.schema import ACTIVE_STATUSES
from utils.errors import log_event
from utils.logcenter import LOG_KEY_AUDIT, delete_log_messages, send_to_log
from utils.logger import logger


async def _delete_open_ticket_channels(guild, tickets) -> int:
    """Удаляет каналы открытых тикетов пользователя. Возвращает число удалённых."""
    removed = 0
    for ticket in tickets:
        if ticket["status"] not in ACTIVE_STATUSES:
            continue
        channel = guild.get_channel(ticket["channel_id"])
        if channel is None:
            continue
        try:
            await channel.delete(reason="Удаление персональных данных заявителя")
            removed += 1
        except discord.NotFound:
            removed += 1  # канала уже нет — цель достигнута
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.exception(
                f"erasure outcome=channel_delete_failed channel_id={ticket['channel_id']} "
                f"error_type={type(error).__name__}"
            )
    return removed


async def erase_user_data(guild, member) -> dict[str, int]:
    """Выполняет удаление данных пользователя в рамках одного сервера.

    Возвращает счётчики обработанных объектов для отчёта администратору.
    """
    user_id = member.id
    tickets = await tickets_db.async_get_user_tickets(guild.id, user_id)

    # открытый тикет: переписка и доступ отзываются вместе с каналом
    channels = await _delete_open_ticket_channels(guild, tickets)

    # транскрипты и прочие логовые сообщения по всем тикетам пользователя
    log_refs = []
    for ticket in tickets:
        log_refs.extend(tickets_db.parse_log_message_refs(ticket["log_message_ids"]))
    log_messages = await delete_log_messages(guild, log_refs) if log_refs else 0

    # записи БД: ответы/имя/ID стираются, открытые становятся закрытыми
    anonymized = await tickets_db.async_anonymize_user_tickets(guild.id, user_id)

    # AFK: ник восстанавливается до удаления записи, пока известен исходный
    afk_row = await afk_db.async_get_afk_user(user_id, guild.id)
    original_nick = afk_row["original_nick"] if afk_row else None
    nick_applied = bool(afk_row["nick_applied"]) if afk_row else False
    afk_counts = await afk_db.async_delete_user_data(user_id, guild.id)
    if afk_row is not None:
        await remove_afk_nickname(member, original_nick, nick_applied=nick_applied)

    return {
        "tickets": anonymized,
        "channels": channels,
        "log_messages": log_messages,
        **afk_counts,
    }


async def audit_erasure(guild, admin, member, counts: dict[str, int]) -> None:
    """Минимальный аудит административного удаления (ID и счётчики, без данных)."""
    embed = discord.Embed(title=config.PRIVACY_AUDIT_TITLE, color=discord.Color.dark_grey())
    embed.add_field(name="Администратор", value=f"<@{admin.id}>", inline=True)
    embed.add_field(name="Субъект данных (ID)", value=str(member.id), inline=True)
    embed.add_field(
        name="Обработано",
        value=(
            f"тикетов — {counts['tickets']}, каналов — {counts['channels']}, "
            f"лог-сообщений — {counts['log_messages']}, AFK-записей — {counts['afk_users']}"
        ),
        inline=False,
    )
    await send_to_log(guild, LOG_KEY_AUDIT, embed=embed)
    log_event(
        "erasure",
        guild_id=getattr(guild, "id", None),
        admin_id=admin.id,
        subject_id=member.id,
        tickets=counts["tickets"],
        channels=counts["channels"],
        log_messages=counts["log_messages"],
        afk_users=counts["afk_users"],
    )
