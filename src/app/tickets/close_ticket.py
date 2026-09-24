"""Кнопка «Закрыть тикет»: идемпотентное терминальное действие."""

from __future__ import annotations

import discord

import config
from database.schema import STATUS_CLOSED
from utils.errors import InteractionErrorBoundary
from utils.logger import logger
from utils.mentions import mentions_for
from utils.permissions import is_staff

from .workflow import (
    claim_ticket,
    complete_terminal_action,
    release_ticket_claim,
    ticket_for_channel,
)


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Закрыть тикет",
            style=discord.ButtonStyle.danger,
            custom_id="ticket_close",
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message(config.TICKET_NO_PERMISSION, ephemeral=True)
            return

        channel = interaction.channel
        guild = interaction.guild
        guild_id = getattr(guild, "id", None)
        channel_id = getattr(channel, "id", None)
        claimed = False

        async with InteractionErrorBoundary(interaction, "ticket.close", channel_id=channel_id):
            ticket = await ticket_for_channel(channel_id, guild_id)
            if ticket is None:
                # кнопка нажата в канале, который не является тикетом этого сервера
                await interaction.response.send_message(config.TICKET_ALREADY_DECIDED, ephemeral=True)
                return

            claimed = await claim_ticket(channel_id, STATUS_CLOSED, guild_id)
            if not claimed:
                await interaction.response.send_message(config.TICKET_ALREADY_DECIDED, ephemeral=True)
                return

            try:
                # отвечаем сразу: дальше канал удалится и отвечать будет некуда
                await interaction.response.send_message("Тикет закрывается...", ephemeral=True)

                embed = discord.Embed(
                    title=config.TICKET_CLOSED_LOG_TITLE,
                    color=discord.Color.dark_grey(),
                )
                embed.add_field(name="Тикет", value=ticket["topic"] or channel.name, inline=True)
                embed.add_field(name="Закрыл", value=interaction.user.mention, inline=True)

                outcome = await complete_terminal_action(
                    guild=guild,
                    channel=channel,
                    status=STATUS_CLOSED,
                    actor=interaction.user,
                    reason=None,
                    embed=embed,
                    after_finalize=lambda: _notify_applicant(guild, ticket),
                )

                if not outcome.ok:
                    await interaction.followup.send(outcome.reason, ephemeral=True)
                    return

                if outcome.transcript_note and not outcome.transcript_note.startswith("✅"):
                    await interaction.followup.send(outcome.transcript_note, ephemeral=True)
            except BaseException:
                if claimed:
                    await release_ticket_claim(channel_id)
                raise


async def _notify_applicant(guild, ticket) -> None:
    """Заявитель должен узнать о закрытии, а не молча потерять канал."""
    if guild is None or ticket is None:
        return
    applicant = guild.get_member(ticket["user_id"])
    if applicant is None:
        return
    try:
        await applicant.send(config.DM_TICKET_CLOSED, allowed_mentions=mentions_for())
    except discord.Forbidden:
        pass  # личка закрыта — ожидаемо
    except discord.HTTPException as error:
        logger.warning(f"ticket.close outcome=dm_failed error_type={type(error).__name__}")
