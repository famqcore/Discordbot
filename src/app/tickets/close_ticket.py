import discord

import config
from database.tickets_db import get_ticket, update_ticket_status
from utils.logcenter import LOG_KEY_DECISIONS, send_to_log
from utils.logger import logger
from utils.permissions import is_staff

from .transcript import build_transcript_file


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

        # отвечаем сразу: дальше канал удалится и отвечать будет некуда
        await interaction.response.send_message("Тикет закрывается...", ephemeral=True)

        channel = interaction.channel
        guild = interaction.guild
        ticket = get_ticket(channel.id)
        files = await build_transcript_file(channel)
        update_ticket_status(channel.id, "closed", closed_by=interaction.user.id)

        if ticket and guild:
            applicant = guild.get_member(ticket["user_id"])
            if applicant:
                try:
                    await applicant.send(config.DM_TICKET_CLOSED)
                except Exception:
                    pass  # личка закрыта — не критично

        embed = discord.Embed(
            title=config.TICKET_CLOSED_LOG_TITLE,
            color=discord.Color.dark_grey(),
        )
        embed.add_field(
            name="Тикет", value=ticket["topic"] if ticket else channel.name, inline=True
        )
        embed.add_field(name="Закрыл", value=interaction.user.mention, inline=True)
        await send_to_log(guild, LOG_KEY_DECISIONS, embed=embed, files=files)

        try:
            await channel.delete(reason=f"Тикет закрыт модератором {interaction.user}")
        except Exception as e:
            logger.error(f"Не удалось удалить канал тикета {channel.id}: {e}")
        logger.info(f"Тикет {channel.id} закрыт модератором {interaction.user}")
