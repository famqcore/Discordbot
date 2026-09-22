"""Создание заявки: идемпотентный workflow с компенсацией (issue #2).

Порядок шагов выбран так, чтобы после любого сбоя пара «состояние БД —
канал Discord» оставалась согласованной:

1. проверка активной заявки (её наличие сразу возвращает ссылку на канал);
2. создание канала — единственный внешний объект, который придётся убирать;
3. запись в БД. Конфликт уникального индекса означает, что параллельный
   submit победил: созданный канал удаляется, пользователь получает ссылку
   на существующую заявку;
4. только после успешной записи — необязательные шаги (роль, DM, карточка,
   пинг, лог). Их сбой не отменяет заявку: канал и запись уже согласованы,
   проблема уходит в лог с correlation id.

Сбой на шаге 2 или 3 компенсируется полностью: канал удаляется, запись
удаляется, ошибка уборки не маскируется. Заявки, пережившие рестарт в
несогласованном виде, подбирает ``tickets/reconcile.py``.
"""

from __future__ import annotations

import json
import re
import sqlite3

import discord

import config
from database.tickets_db import add_log_message_id, get_open_ticket_for_user, save_ticket
from utils import clock
from utils.errors import log_event, new_correlation_id
from utils.logcenter import LOG_KEY_TICKETS, send_to_log
from utils.logger import logger
from utils.mentions import mentions_for
from utils.resolve import get_category, get_role

from .views import FullTicketView

PING_ROLE_SPECS = (
    ("ROLE_RECRUITER_ID", "ROLE_RECRUITER"),
    ("ROLE_OWNER_ID", "ROLE_OWNER"),
    ("ROLE_DEP_OWNER_ID", "ROLE_DEP_OWNER"),
)

ACCESS_ROLE_SPECS = PING_ROLE_SPECS + (
    ("ROLE_ADMIN_ID", "ROLE_ADMIN"),
    ("ROLE_SUPPORT_ID", "ROLE_SUPPORT"),
)


def sanitize_channel_name(text: str) -> str:
    """Имя канала из ника игрока: Discord не переваривает пробелы и спецсимволы."""
    text = re.sub(r"\s+", "-", text.lower().strip())
    text = re.sub(r"[^a-z0-9а-яё_-]", "", text)
    return text.strip("-")[:90] or "user"


class TicketModal(discord.ui.Modal):
    def __init__(self, title, ticket_type, fields):
        super().__init__(title=title)
        self.ticket_type = ticket_type
        self.inputs = {}

        for label, placeholder, required, max_length in fields:
            style = discord.TextStyle.paragraph if max_length > 150 else discord.TextStyle.short
            inp = discord.ui.TextInput(
                label=label,
                placeholder=placeholder,
                style=style,
                required=required,
                max_length=max_length,
            )
            self.inputs[label] = inp
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        log_event(
            "ticket.submit",
            guild_id=getattr(interaction, "guild_id", None),
            user_id=getattr(interaction.user, "id", None),
            ticket_type=self.ticket_type,
        )
        await create_ticket(interaction, self.title, self.ticket_type, self.inputs)


def _resolve_roles(guild, specs) -> list[discord.Role]:
    roles = []
    for id_attr, name_attr in specs:
        role = get_role(guild, getattr(config, id_attr), getattr(config, name_attr))
        if role is not None:
            roles.append(role)
    return roles


def _build_overwrites(guild, member) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in _resolve_roles(guild, ACCESS_ROLE_SPECS):
        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return overwrites


async def _delete_channel(channel, reason: str, correlation_id: str) -> bool:
    """Компенсация: удаляет созданный канал. Ошибку уборки не прячем."""
    if channel is None:
        return True
    try:
        await channel.delete(reason=reason)
        return True
    except discord.NotFound:
        return True
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.exception(
            f"ticket.create outcome=cleanup_failed error_type={type(error).__name__} "
            f"channel_id={getattr(channel, 'id', None)} correlation_id={correlation_id}"
        )
        return False


async def _reply(interaction: discord.Interaction, content: str) -> None:
    try:
        await interaction.edit_original_response(content=content)
    except discord.HTTPException as error:
        logger.warning(f"ticket.create outcome=reply_failed error_type={type(error).__name__}")


async def create_ticket(interaction, topic, ticket_type, inputs):
    guild = interaction.guild
    member = interaction.user
    correlation_id = new_correlation_id()

    try:
        await interaction.response.send_message("Создаю заявку...", ephemeral=True)
    except discord.InteractionResponded:
        pass

    if guild is None:
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    guild_id = getattr(guild, "id", None)
    user_id = getattr(member, "id", None)
    if not isinstance(guild_id, int) or not isinstance(user_id, int):
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    existing = get_open_ticket_for_user(guild_id, user_id)
    if existing:
        await _reply(
            interaction,
            config.TICKET_ALREADY_OPEN.format(channel=f"<#{existing['channel_id']}>"),
        )
        return None

    channel = None
    saved = False
    try:
        category = get_category(guild, config.TICKETS_CATEGORY_ID, config.TICKETS_CATEGORY_NAME)
        if category is None and config.TICKETS_CATEGORY_ID is None:
            category = await guild.create_category(config.TICKETS_CATEGORY_NAME)

        answers = {label: inp.value for label, inp in inputs.items()}
        channel_name = f"{ticket_type}-{sanitize_channel_name(member.name)}"
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=_build_overwrites(guild, member),
            reason=f"Заявка {ticket_type} от {user_id}",
        )

        try:
            save_ticket(
                channel.id,
                user_id,
                member.name,
                topic,
                ticket_type,
                json.dumps(answers, ensure_ascii=False),
                clock.to_db(),
                guild_id=guild_id,
            )
        except sqlite3.IntegrityError:
            # параллельный submit успел раньше: убираем лишний канал и
            # показываем ссылку на реально существующую заявку
            await _delete_channel(channel, "Duplicate open ticket prevented", correlation_id)
            channel = None
            current = get_open_ticket_for_user(guild_id, user_id)
            link = f"<#{current['channel_id']}>" if current else "уже открыта"
            await _reply(interaction, config.TICKET_ALREADY_OPEN.format(channel=link))
            log_event(
                "ticket.create",
                outcome="duplicate",
                guild_id=guild_id,
                user_id=user_id,
                correlation_id=correlation_id,
            )
            return None
        saved = True

    except (discord.Forbidden, discord.HTTPException, sqlite3.Error) as error:
        logger.exception(
            f"ticket.create outcome=error error_type={type(error).__name__} "
            f"guild_id={guild_id} user_id={user_id} correlation_id={correlation_id}"
        )
        if saved:
            await _rollback_saved_ticket(channel, correlation_id)
        else:
            await _delete_channel(channel, "Rollback failed ticket creation", correlation_id)
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    # С этого момента заявка существует и согласована с каналом.
    # Оставшиеся шаги необязательны: их сбой логируется, но не откатывает тикет.
    await _post_create_steps(interaction, guild, member, channel, topic, ticket_type, answers)

    await _reply(interaction, f"Заявка создана! {channel.mention}")
    log_event(
        "ticket.create",
        guild_id=guild_id,
        user_id=user_id,
        channel_id=channel.id,
        ticket_type=ticket_type,
    )
    return channel


async def _rollback_saved_ticket(channel, correlation_id: str) -> None:
    """Сбой после записи в БД: снимаем и запись, и канал."""
    from database.tickets_db import delete_ticket

    channel_id = getattr(channel, "id", None)
    removed = await _delete_channel(channel, "Rollback failed ticket creation", correlation_id)
    if channel_id is not None:
        try:
            delete_ticket(channel_id)
        except sqlite3.Error as error:
            logger.exception(
                f"ticket.create outcome=db_rollback_failed error_type={type(error).__name__} "
                f"channel_id={channel_id} correlation_id={correlation_id}"
            )
            return
    if not removed:
        logger.error(
            f"ticket.create outcome=orphan_channel channel_id={channel_id} "
            f"correlation_id={correlation_id} — канал остался без записи, "
            "reconciliation удалит его при следующем проходе"
        )


async def _post_create_steps(interaction, guild, member, channel, topic, ticket_type, answers):
    """Необязательные шаги: роль, DM, карточка, пинг, лог."""
    guild_id = guild.id

    apply_role = get_role(guild, config.ROLE_APPLIED_ID, config.ROLE_APPLIED)
    if apply_role is not None and apply_role < guild.me.top_role:
        try:
            await member.add_roles(apply_role, reason="Подана заявка")
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning(
                f"ticket.create outcome=role_failed error_type={type(error).__name__} "
                f"guild_id={guild_id} role_id={apply_role.id}"
            )

    try:
        await member.send(config.DM_MESSAGE)
    except discord.Forbidden:
        pass  # личка закрыта — ожидаемо
    except discord.HTTPException as error:
        logger.warning(f"ticket.create outcome=dm_failed error_type={type(error).__name__}")

    embed = discord.Embed(title=topic, color=discord.Color.gold(), timestamp=clock.utcnow())
    embed.add_field(name="От кого", value=member.mention, inline=False)
    for label, value in answers.items():
        # field value ограничен 1024 символами у Discord
        embed.add_field(
            name=label, value=(value or "—")[: config.DISCORD_EMBED_FIELD_VALUE_MAX], inline=False
        )

    try:
        await channel.send(embed=embed, view=FullTicketView())
        ping_roles = _resolve_roles(guild, PING_ROLE_SPECS)
        if ping_roles:
            # служебный пинг рекрутёров: адресный AllowedMentions,
            # массовые упоминания запрещены на уровне клиента
            await channel.send(
                f"{member.mention} {' '.join(role.mention for role in ping_roles)}",
                allowed_mentions=mentions_for(users=[member], roles=ping_roles),
            )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.exception(
            f"ticket.create outcome=card_failed error_type={type(error).__name__} "
            f"guild_id={guild_id} channel_id={channel.id}"
        )

    log_embed = discord.Embed(
        title=f"📥 Новая заявка: {ticket_type}",
        color=discord.Color.gold(),
        timestamp=clock.utcnow(),
    )
    log_embed.add_field(name="Заявитель", value=member.mention, inline=True)
    log_embed.add_field(name="Тикет", value=channel.mention, inline=True)
    log_message = await send_to_log(guild, LOG_KEY_TICKETS, embed=log_embed)
    if log_message is not None:
        # связь с логом нужна, чтобы удаление данных стирало вложения
        add_log_message_id(channel.id, log_message.channel.id, log_message.id)
