"""Решение по заявке: «Принять» / «Отказать»."""

from __future__ import annotations

from dataclasses import dataclass

import discord

import config
from database.schema import STATUS_ACCEPTED, STATUS_DENIED
from utils import clock
from utils.errors import InteractionErrorBoundary
from utils.logger import logger
from utils.mentions import escape_user_text, mentions_for
from utils.permissions import is_staff

from .workflow import (
    claim_ticket,
    complete_terminal_action,
    release_ticket_claim,
    ticket_for_channel,
)


@dataclass(frozen=True)
class Decision:
    status: str
    modal_title: str
    reason_label: str
    embed_title: str
    embed_color: discord.Color
    channel_note: str
    reply_text: str
    log_text: str
    dm_text: str


ACCEPT = Decision(
    status=STATUS_ACCEPTED,
    modal_title="Принятие заявки",
    reason_label="Причина принятия",
    embed_title=config.ACCEPT_EMBED_TITLE,
    embed_color=discord.Color.green(),
    channel_note="✅ Заявка принята! {mention}",
    reply_text="Заявка принята",
    log_text="принят",
    dm_text=config.DM_TICKET_ACCEPTED,
)

DENY = Decision(
    status=STATUS_DENIED,
    modal_title="Отклонение заявки",
    reason_label="Причина отказа",
    embed_title=config.DENY_EMBED_TITLE,
    embed_color=discord.Color.red(),
    channel_note="❌ Заявка отклонена! Причина: {reason}",
    reply_text="Заявка отклонена",
    log_text="отклонён",
    dm_text=config.DM_TICKET_DENIED,
)


class DecisionReasonModal(discord.ui.Modal):
    def __init__(self, channel, decision):
        super().__init__(title=decision.modal_title)
        self.channel = channel
        self.decision = decision
        self.reason = discord.ui.TextInput(
            label=decision.reason_label,
            placeholder="Укажите причину",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=config.TICKET_DECISION_REASON_MAX_LENGTH,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        decision = self.decision
        channel_id = getattr(self.channel, "id", None)
        guild_id = getattr(guild, "id", None)
        claimed = False

        async with InteractionErrorBoundary(
            interaction, "ticket.decision", channel_id=channel_id, status=decision.status
        ):
            ticket = await ticket_for_channel(channel_id, guild_id)
            if ticket is None:
                await interaction.response.send_message(config.TICKET_ALREADY_DECIDED, ephemeral=True)
                return

            # Захват заявки: из накликанных accept/deny/close побеждает ровно один.
            claimed = await claim_ticket(channel_id, decision.status, guild_id)
            if not claimed:
                await interaction.response.send_message(config.TICKET_ALREADY_DECIDED, ephemeral=True)
                return

            try:
                await interaction.response.send_message(
                    f"{decision.reply_text}. Тикет обрабатывается…", ephemeral=True
                )

                applicant = guild.get_member(ticket["user_id"]) if guild else None
                mention = applicant.mention if applicant else "—"
                # причина — ввод модератора, но доверять ему нельзя: экранируем,
                # чтобы из решения нельзя было собрать массовый пинг
                reason = escape_user_text(self.reason.value)

                async def notify_applicant_and_channel() -> None:
                    await _notify_applicant(applicant, decision, reason)
                    await _announce_in_channel(self.channel, decision, mention, reason, applicant)

                embed = discord.Embed(
                    title=decision.embed_title,
                    color=decision.embed_color,
                    timestamp=clock.utcnow(),
                )
                embed.add_field(name="Заявитель", value=mention, inline=False)
                embed.add_field(name="Причина", value=reason, inline=False)
                embed.add_field(name="Рекрут", value=interaction.user.mention, inline=False)

                outcome = await complete_terminal_action(
                    guild=guild,
                    channel=self.channel,
                    status=decision.status,
                    actor=interaction.user,
                    reason=self.reason.value,
                    embed=embed,
                    after_finalize=notify_applicant_and_channel,
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


async def _notify_applicant(applicant, decision: Decision, reason: str) -> None:
    """Уведомляет заявителя о уже зафиксированном решении до удаления канала."""
    if applicant is None:
        return
    try:
        await applicant.send(
            decision.dm_text.format(reason=reason), allowed_mentions=mentions_for()
        )
    except discord.Forbidden:
        pass  # личка закрыта — ожидаемо
    except discord.HTTPException as error:
        logger.warning(f"ticket.decision outcome=dm_failed error_type={type(error).__name__}")


async def _announce_in_channel(channel, decision, mention, reason, applicant) -> None:
    try:
        await channel.send(
            decision.channel_note.format(mention=mention, reason=reason),
            allowed_mentions=mentions_for(users=[applicant] if applicant else []),
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning(f"ticket.decision outcome=announce_failed error_type={type(error).__name__}")


class DecisionButton(discord.ui.Button):
    decision = None

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message(config.TICKET_NO_PERMISSION, ephemeral=True)
            return
        modal = DecisionReasonModal(interaction.channel, self.decision)
        await interaction.response.send_modal(modal)


class AcceptButton(DecisionButton):
    decision = ACCEPT

    def __init__(self):
        super().__init__(
            label="Принять", style=discord.ButtonStyle.success, custom_id="ticket_accept"
        )


class DenyButton(DecisionButton):
    decision = DENY

    def __init__(self):
        super().__init__(
            label="Отказать", style=discord.ButtonStyle.danger, custom_id="ticket_deny"
        )
