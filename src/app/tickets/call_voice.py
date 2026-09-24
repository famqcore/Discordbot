"""Кнопка «Вызвать на обзвон»: приглашение заявителя в голосовой канал."""

from __future__ import annotations

import discord

import config
from database.tickets_db import async_get_ticket
from utils.errors import InteractionErrorBoundary, log_event
from utils.logcenter import LOG_KEY_CALLS, send_to_log
from utils.mentions import mentions_for
from utils.permissions import is_staff
from utils.ratelimit import retry_after
from utils.resolve import get_voice_channel


class VoiceCallButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Вызвать на обзвон",
            style=discord.ButtonStyle.primary,
            custom_id="ticket_voice",
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message(config.TICKET_NO_PERMISSION, ephemeral=True)
            return
        view = VoiceSelectView(interaction.channel)
        await interaction.response.send_message("Выберите канал:", view=view, ephemeral=True)
        view.bind_original_response(interaction)


class VoiceSelectView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=config.VOICE_SELECT_VIEW_TIMEOUT_SECONDS)
        self.ticket_channel = channel
        self._original_interaction: discord.Interaction | None = None
        for i, name in enumerate(config.VOICE_CHANNELS):
            btn = discord.ui.Button(
                label=name, style=discord.ButtonStyle.success, custom_id=f"voice{i + 1}"
            )
            btn.callback = self.make_callback(name, i)
            self.add_item(btn)

    def bind_original_response(self, interaction: discord.Interaction) -> None:
        """Сохраняет interaction для редактирования ephemeral-ответа."""
        self._original_interaction = interaction

    async def on_timeout(self) -> None:
        if self._original_interaction is None:
            return
        try:
            await self._original_interaction.edit_original_response(
                content=config.VOICE_SELECT_EXPIRED,
                view=None,
            )
        except discord.HTTPException:
            pass

    def make_callback(self, voice_name, index):
        async def callback(interaction: discord.Interaction):
            async with InteractionErrorBoundary(interaction, "ticket.call_voice"):
                await self._invite(interaction, voice_name, index)

        return callback

    async def _invite(self, interaction: discord.Interaction, voice_name: str, index: int) -> None:
        guild_id = getattr(interaction.guild, "id", None)
        channel_id = getattr(self.ticket_channel, "id", None)
        if isinstance(guild_id, int) and isinstance(channel_id, int):
            wait = retry_after(
                ("ticket_voice", guild_id, channel_id),
                config.VOICE_CALL_BUTTON_COOLDOWN_SECONDS,
            )
            if wait:
                await interaction.response.send_message(
                    f"⏳ Подождите {wait} сек. перед следующим вызовом на обзвон.",
                    ephemeral=True,
                )
                return

        ticket = await async_get_ticket(self.ticket_channel.id)
        applicant = interaction.guild.get_member(ticket["user_id"]) if ticket else None
        recruiter = interaction.user

        voice_id = (
            config.VOICE_CHANNEL_IDS[index] if index < len(config.VOICE_CHANNEL_IDS) else None
        )
        voice_ch = get_voice_channel(interaction.guild, voice_id, voice_name)

        if not voice_ch:
            await self.ticket_channel.send(f"❌ Канал {voice_name} не найден!")
            await interaction.response.send_message(f"Канал {voice_name} не найден", ephemeral=True)
            return

        # пинг по делу — только заявителю, рекрутёр нажал кнопку сам
        await self.ticket_channel.send(
            f"**Рекрут** {recruiter.mention} **вызвал** "
            f"{applicant.mention if applicant else 'заявителя'} **на обзвон**",
            allowed_mentions=mentions_for(users=[applicant] if applicant else []),
        )
        await self.ticket_channel.send(
            f"{applicant.mention if applicant else 'Заявитель'} зайдите в {voice_ch.mention}",
            allowed_mentions=mentions_for(users=[applicant] if applicant else []),
        )
        await interaction.response.send_message(
            f"Вызов отправлен в {voice_ch.mention}", ephemeral=True
        )

        log_embed = discord.Embed(title="🔊 Вызов на обзвон", color=discord.Color.blue())
        log_embed.add_field(name="Рекрут", value=recruiter.mention, inline=True)
        log_embed.add_field(
            name="Заявитель",
            value=applicant.mention if applicant else "—",
            inline=True,
        )
        log_embed.add_field(name="Канал", value=voice_ch.mention, inline=True)
        await send_to_log(interaction.guild, LOG_KEY_CALLS, embed=log_embed)

        log_event(
            "ticket.call_voice",
            guild_id=getattr(interaction.guild, "id", None),
            channel_id=getattr(self.ticket_channel, "id", None),
            voice_channel_id=voice_ch.id,
            actor_id=recruiter.id,
        )
