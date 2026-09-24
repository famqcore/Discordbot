"""Создание заявок и компенсация при сбоях внешних операций."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import discord

import config
from database.tickets_db import (
    async_add_log_message_id,
    async_delete_ticket,
    async_get_open_ticket_for_user,
    async_save_ticket,
)
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


@dataclass(frozen=True)
class TicketSubmission:
    """Данные одной заполненной формы до создания канала."""

    form: config.TicketForm
    answers: Mapping[str, str]

    @property
    def topic(self) -> str:
        return self.form.title

    @property
    def ticket_type(self) -> str:
        return self.form.ticket_type


@dataclass(frozen=True)
class CreatedTicket:
    """Согласованные объекты созданной заявки."""

    guild: discord.Guild
    applicant: discord.Member
    channel: discord.TextChannel
    submission: TicketSubmission


def sanitize_channel_name(text: str) -> str:
    """Возвращает допустимую для Discord часть имени канала."""
    normalized = re.sub(r"\s+", "-", text.lower().strip())
    sanitized = re.sub(r"[^a-z0-9а-яё_-]", "", normalized).strip("-")
    return sanitized[: config.TICKET_CHANNEL_SLUG_MAX_LENGTH] or "user"


class TicketModal(discord.ui.Modal):
    def __init__(self, form: config.TicketForm) -> None:
        super().__init__(title=form.title)
        self.form = form
        self.ticket_type = form.ticket_type
        self.inputs: dict[str, discord.ui.TextInput] = {}

        for field in form.fields:
            style = (
                discord.TextStyle.paragraph
                if field.max_length >= config.TICKET_MULTILINE_FIELD_MIN_LENGTH
                else discord.TextStyle.short
            )
            text_input = discord.ui.TextInput(
                label=field.label,
                placeholder=field.placeholder,
                style=style,
                required=field.required,
                max_length=field.max_length,
            )
            self.inputs[field.label] = text_input
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        submission = TicketSubmission(
            form=self.form,
            answers={label: text_input.value for label, text_input in self.inputs.items()},
        )
        log_event(
            "ticket.submit",
            guild_id=getattr(interaction, "guild_id", None),
            user_id=getattr(interaction.user, "id", None),
            ticket_type=submission.ticket_type,
        )
        await create_ticket(interaction, submission)


def _resolve_roles(guild: discord.Guild, specs: tuple[tuple[str, str], ...]) -> list[discord.Role]:
    return [
        role
        for id_attr, name_attr in specs
        if (role := get_role(guild, getattr(config, id_attr), getattr(config, name_attr)))
        is not None
    ]


def _build_overwrites(
    guild: discord.Guild, applicant: discord.Member
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        applicant: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in _resolve_roles(guild, ACCESS_ROLE_SPECS):
        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return overwrites


async def _delete_channel(channel: Any, reason: str, correlation_id: str) -> bool:
    """Удаляет канал, созданный неуспешной операцией."""
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


async def _create_ticket_channel(
    guild: discord.Guild,
    applicant: discord.Member,
    submission: TicketSubmission,
) -> discord.TextChannel:
    category = get_category(guild, config.TICKETS_CATEGORY_ID, config.TICKETS_CATEGORY_NAME)
    if category is None and config.TICKETS_CATEGORY_ID is None:
        category = await guild.create_category(config.TICKETS_CATEGORY_NAME)

    channel_name = f"{submission.ticket_type}-{sanitize_channel_name(applicant.name)}"
    return await guild.create_text_channel(
        channel_name,
        category=category,
        overwrites=_build_overwrites(guild, applicant),
        reason=f"Заявка {submission.ticket_type} от {applicant.id}",
    )


async def create_ticket(
    interaction: discord.Interaction, submission: TicketSubmission
) -> discord.TextChannel | None:
    """Создаёт заявку, компенсируя канал при ошибке записи в SQLite."""
    guild = interaction.guild
    applicant = interaction.user
    correlation_id = new_correlation_id()

    try:
        await interaction.response.send_message("Создаю заявку...", ephemeral=True)
    except discord.InteractionResponded:
        pass

    if guild is None:
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    guild_id = getattr(guild, "id", None)
    user_id = getattr(applicant, "id", None)
    if not isinstance(guild_id, int) or not isinstance(user_id, int):
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    existing = await async_get_open_ticket_for_user(guild_id, user_id)
    if existing:
        await _reply(
            interaction,
            config.TICKET_ALREADY_OPEN.format(channel=f"<#{existing['channel_id']}>"),
        )
        return None

    channel = None
    try:
        channel = await _create_ticket_channel(guild, applicant, submission)
        await async_save_ticket(
            channel.id,
            user_id,
            applicant.name,
            submission.topic,
            submission.ticket_type,
            json.dumps(submission.answers, ensure_ascii=False),
            clock.to_db(),
            guild_id=guild_id,
        )
    except sqlite3.IntegrityError:
        await _delete_channel(channel, "Duplicate open ticket prevented", correlation_id)
        current = await async_get_open_ticket_for_user(guild_id, user_id)
        channel_link = f"<#{current['channel_id']}>" if current else "уже открыта"
        await _reply(interaction, config.TICKET_ALREADY_OPEN.format(channel=channel_link))
        log_event(
            "ticket.create",
            outcome="duplicate",
            guild_id=guild_id,
            user_id=user_id,
            correlation_id=correlation_id,
        )
        return None
    except (discord.Forbidden, discord.HTTPException, sqlite3.Error) as error:
        logger.exception(
            f"ticket.create outcome=error error_type={type(error).__name__} "
            f"guild_id={guild_id} user_id={user_id} correlation_id={correlation_id}"
        )
        await _rollback_saved_ticket(channel, correlation_id)
        await _reply(interaction, config.ERROR_TICKET_CREATE)
        return None

    ticket = CreatedTicket(guild, applicant, channel, submission)
    await _post_create_steps(ticket)
    await _reply(interaction, f"Заявка создана! {channel.mention}")
    log_event(
        "ticket.create",
        guild_id=guild_id,
        user_id=user_id,
        channel_id=channel.id,
        ticket_type=submission.ticket_type,
    )
    return channel


async def _rollback_saved_ticket(channel: Any, correlation_id: str) -> None:
    """Удаляет канал и запись после неудачного сохранения заявки."""
    channel_id = getattr(channel, "id", None)
    removed = await _delete_channel(channel, "Rollback failed ticket creation", correlation_id)
    if channel_id is not None:
        try:
            await async_delete_ticket(channel_id)
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


async def _post_create_steps(ticket: CreatedTicket) -> None:
    """Выполняет необязательные уведомления после согласованного создания."""
    await _grant_applied_role(ticket)
    await _notify_applicant(ticket.applicant)
    await _send_ticket_card(ticket)
    await _write_creation_log(ticket)


async def _grant_applied_role(ticket: CreatedTicket) -> None:
    role = get_role(ticket.guild, config.ROLE_APPLIED_ID, config.ROLE_APPLIED)
    if role is None or not role < ticket.guild.me.top_role:
        return
    try:
        await ticket.applicant.add_roles(role, reason="Подана заявка")
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning(
            f"ticket.create outcome=role_failed error_type={type(error).__name__} "
            f"guild_id={ticket.guild.id} role_id={role.id}"
        )


async def _notify_applicant(applicant: discord.Member) -> None:
    try:
        await applicant.send(config.DM_MESSAGE)
    except discord.Forbidden:
        return
    except discord.HTTPException as error:
        logger.warning(f"ticket.create outcome=dm_failed error_type={type(error).__name__}")


def _ticket_embed(ticket: CreatedTicket) -> discord.Embed:
    embed = discord.Embed(
        title=ticket.submission.topic,
        color=discord.Color.gold(),
        timestamp=clock.utcnow(),
    )
    embed.add_field(name="От кого", value=ticket.applicant.mention, inline=False)
    for label, value in ticket.submission.answers.items():
        embed.add_field(
            name=label,
            value=(value or "—")[: config.DISCORD_EMBED_FIELD_VALUE_MAX],
            inline=False,
        )
    return embed


async def _send_ticket_card(ticket: CreatedTicket) -> None:
    try:
        await ticket.channel.send(embed=_ticket_embed(ticket), view=FullTicketView())
        ping_roles = _resolve_roles(ticket.guild, PING_ROLE_SPECS)
        if ping_roles:
            await ticket.channel.send(
                f"{ticket.applicant.mention} {' '.join(role.mention for role in ping_roles)}",
                allowed_mentions=mentions_for(users=[ticket.applicant], roles=ping_roles),
            )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.exception(
            f"ticket.create outcome=card_failed error_type={type(error).__name__} "
            f"guild_id={ticket.guild.id} channel_id={ticket.channel.id}"
        )


async def _write_creation_log(ticket: CreatedTicket) -> None:
    embed = discord.Embed(
        title=f"📥 Новая заявка: {ticket.submission.ticket_type}",
        color=discord.Color.gold(),
        timestamp=clock.utcnow(),
    )
    embed.add_field(name="Заявитель", value=ticket.applicant.mention, inline=True)
    embed.add_field(name="Тикет", value=ticket.channel.mention, inline=True)
    log_message = await send_to_log(ticket.guild, LOG_KEY_TICKETS, embed=embed)
    if log_message is not None:
        await async_add_log_message_id(ticket.channel.id, log_message.channel.id, log_message.id)
