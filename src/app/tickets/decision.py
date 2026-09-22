from dataclasses import dataclass
from datetime import datetime

import discord

import config
from database.tickets_db import get_ticket, update_ticket_status
from utils.logcenter import LOG_KEY_DECISIONS, send_to_log
from utils.logger import logger
from utils.permissions import is_staff

from .transcript import build_transcript_file


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
    status="accepted",
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
    status="denied",
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
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        decision = self.decision

        updated = update_ticket_status(
            self.channel.id, decision.status, interaction.user.id, self.reason.value
        )
        if not updated:
            await interaction.response.send_message(config.TICKET_ALREADY_DECIDED, ephemeral=True)
            return

        ticket = get_ticket(self.channel.id)
        applicant = guild.get_member(ticket["user_id"]) if ticket and guild else None
        mention = applicant.mention if applicant else "—"

        # заявитель должен узнать о решении, а не только молча исчезнуть вместе с каналом
        if applicant:
            try:
                await applicant.send(decision.dm_text.format(reason=self.reason.value))
            except Exception:
                pass  # личка закрыта — не критично

        files = await build_transcript_file(self.channel)

        embed = discord.Embed(
            title=decision.embed_title,
            color=decision.embed_color,
            timestamp=datetime.now(),
        )
        embed.add_field(name="Заявитель", value=mention, inline=False)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)
        embed.add_field(name="Рекрут", value=interaction.user.mention, inline=False)
        await send_to_log(guild, LOG_KEY_DECISIONS, embed=embed, files=files)

        await self.channel.send(
            decision.channel_note.format(mention=mention, reason=self.reason.value)
        )
        await interaction.response.send_message(
            f"{decision.reply_text}. Тикет удаляется.", ephemeral=True
        )

        logger.info(f"Тикет {self.channel.id} {decision.log_text}, удаляю канал")
        await self.channel.delete()


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
