import json
import re
import sqlite3
from datetime import datetime

import discord

import config
from database.tickets_db import add_log_message_id, get_open_ticket_for_user, save_ticket
from utils.logcenter import LOG_KEY_TICKETS, send_to_log
from utils.logger import logger
from utils.mentions import mentions_for
from utils.resolve import get_category, get_role

from .views import FullTicketView


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
        logger.info(f"Пользователь {interaction.user} подал {self.ticket_type} заявку")
        await create_ticket(interaction, self.title, self.ticket_type, self.inputs)


async def create_ticket(interaction, topic, ticket_type, inputs):
    guild = interaction.guild
    member = interaction.user
    channel = None

    try:
        await interaction.response.send_message("Создаю заявку...", ephemeral=True)
    except discord.InteractionResponded:
        pass

    if guild is None:
        await interaction.edit_original_response(content=config.ERROR_TICKET_CREATE)
        return

    guild_id = getattr(guild, "id", None)
    user_id = getattr(member, "id", None)
    if isinstance(guild_id, int) and isinstance(user_id, int):
        existing = get_open_ticket_for_user(guild_id, user_id)
        if existing:
            await interaction.edit_original_response(
                content=config.TICKET_ALREADY_OPEN.format(channel=f"<#{existing['channel_id']}>")
            )
            return

    try:
        apply_role = get_role(guild, config.ROLE_APPLIED_ID, config.ROLE_APPLIED)
        if apply_role and apply_role < guild.me.top_role:
            try:
                await member.add_roles(apply_role)
            except discord.Forbidden:
                logger.warning(f"Нет прав на выдачу роли {apply_role.name}")

        try:
            dm = await member.create_dm()
            await dm.send(config.DM_MESSAGE)
        except discord.Forbidden:
            pass

        category = get_category(guild, config.TICKETS_CATEGORY_ID, config.TICKETS_CATEGORY_NAME)
        if not category:
            category = await guild.create_category(config.TICKETS_CATEGORY_NAME)

        answers = {label: inp.value for label, inp in inputs.items()}
        channel_name = f"{ticket_type}-{sanitize_channel_name(member.name)}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        for role_id, role_name in [
            (config.ROLE_RECRUITER_ID, config.ROLE_RECRUITER),
            (config.ROLE_OWNER_ID, config.ROLE_OWNER),
            (config.ROLE_DEP_OWNER_ID, config.ROLE_DEP_OWNER),
            (config.ROLE_ADMIN_ID, config.ROLE_ADMIN),
            (config.ROLE_SUPPORT_ID, config.ROLE_SUPPORT),
        ]:
            role = get_role(guild, role_id, role_name)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )

        channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites
        )

        try:
            save_ticket(
                channel.id,
                member.id,
                member.name,
                topic,
                ticket_type,
                json.dumps(answers, ensure_ascii=False),
                datetime.now().isoformat(),
                guild_id=guild_id if isinstance(guild_id, int) else 0,
            )
        except sqlite3.IntegrityError:
            await channel.delete(reason="Duplicate open ticket prevented")
            channel = None
            await interaction.edit_original_response(
                content=config.TICKET_ALREADY_OPEN.format(channel="уже открыта")
            )
            return

        embed = discord.Embed(title=topic, color=discord.Color.gold(), timestamp=datetime.now())
        embed.add_field(name="От кого", value=member.mention, inline=False)
        for label, value in answers.items():
            # field value ограничен 1024 символами у Discord
            embed.add_field(name=label, value=(value or "—")[:1024], inline=False)

        ping_roles = []
        for role_id, role_name in [
            (config.ROLE_RECRUITER_ID, config.ROLE_RECRUITER),
            (config.ROLE_OWNER_ID, config.ROLE_OWNER),
            (config.ROLE_DEP_OWNER_ID, config.ROLE_DEP_OWNER),
        ]:
            role = get_role(guild, role_id, role_name)
            if role:
                ping_roles.append(role)

        await channel.send(embed=embed, view=FullTicketView())
        if ping_roles:
            # служебный пинг рекрутёров: адресный AllowedMentions,
            # массовые упоминания запрещены на уровне клиента
            await channel.send(
                f"{member.mention} {' '.join(role.mention for role in ping_roles)}",
                allowed_mentions=mentions_for(users=[member], roles=ping_roles),
            )

        log_embed = discord.Embed(
            title=f"📥 Новая заявка: {ticket_type}",
            color=discord.Color.gold(),
            timestamp=datetime.now(),
        )
        log_embed.add_field(name="Заявитель", value=member.mention, inline=True)
        log_embed.add_field(name="Тикет", value=channel.mention, inline=True)
        log_message = await send_to_log(guild, LOG_KEY_TICKETS, embed=log_embed)
        if log_message is not None:
            # связь с логом нужна, чтобы удаление данных стирало вложения
            add_log_message_id(channel.id, log_message.channel.id, log_message.id)

        await interaction.edit_original_response(content=f"Заявка создана! {channel.mention}")
        logger.info(f"Заявка {ticket_type} создана для {member.name} в {channel.name}")

    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")
        if channel is not None:
            try:
                await channel.delete(reason="Rollback failed ticket creation")
            except Exception as cleanup_error:
                logger.error(
                    f"Не удалось удалить частично созданный тикет {channel.id}: {cleanup_error}"
                )
        await interaction.edit_original_response(content=config.ERROR_TICKET_CREATE)
