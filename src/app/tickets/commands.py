import discord
from discord.ext import commands

import config
from database.afk_db import delete_user_data as delete_afk_user_data
from database.tickets_db import (
    anonymize_user_tickets,
    get_all_tickets,
    get_open_ticket_for_user,
    get_stats,
)
from utils.ratelimit import retry_after

from .create_ticket import TicketModal


class TicketTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        rp = discord.ui.Button(
            label=config.TICKET_RP_TITLE, style=discord.ButtonStyle.success, custom_id="rp"
        )
        rp.callback = self.rp_callback
        self.add_item(rp)

        capt = discord.ui.Button(
            label=config.TICKET_CAPT_TITLE, style=discord.ButtonStyle.primary, custom_id="capt"
        )
        capt.callback = self.capt_callback
        self.add_item(capt)

    async def _send_modal_or_existing_ticket(
        self,
        interaction: discord.Interaction,
        title: str,
        ticket_type: str,
        fields: list,
    ):
        guild_id = getattr(interaction, "guild_id", None) or getattr(
            getattr(interaction, "guild", None), "id", None
        )
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        if isinstance(guild_id, int) and isinstance(user_id, int):
            wait = retry_after(
                ("ticket_type", guild_id, user_id), config.TICKET_BUTTON_COOLDOWN_SECONDS
            )
            if wait:
                await interaction.response.send_message(
                    f"⏳ Подождите {wait} сек. перед повторной отправкой формы.",
                    ephemeral=True,
                )
                return

            existing = get_open_ticket_for_user(guild_id, user_id)
            if existing:
                channel = f"<#{existing['channel_id']}>"
                await interaction.response.send_message(
                    config.TICKET_ALREADY_OPEN.format(channel=channel), ephemeral=True
                )
                return

        await interaction.response.send_modal(TicketModal(title, ticket_type, fields))

    async def rp_callback(self, interaction: discord.Interaction):
        await self._send_modal_or_existing_ticket(
            interaction,
            config.TICKET_RP_TITLE,
            "rp",
            config.RP_FIELDS,
        )

    async def capt_callback(self, interaction: discord.Interaction):
        await self._send_modal_or_existing_ticket(
            interaction,
            config.TICKET_CAPT_TITLE,
            "capt",
            config.CAPT_FIELDS,
        )


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name=config.CMD_FAMQCORE)
    @commands.guild_only()
    @commands.cooldown(1, config.FAMQCORE_COMMAND_COOLDOWN_SECONDS, commands.BucketType.channel)
    async def famqcore_apply(self, ctx):
        embed = discord.Embed(
            title=config.FAMQCORE_EMBED_TITLE,
            description=config.FAMQCORE_EMBED_DESCRIPTION,
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed, view=TicketTypeView())

    @commands.command(name=config.CMD_STATS)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def show_stats(self, ctx):
        stats = get_stats(ctx.guild.id)
        embed = discord.Embed(title="Статистика заявок", color=discord.Color.gold())
        embed.add_field(name="Всего", value=stats["total"], inline=True)
        embed.add_field(name="Принято", value=stats["accepted"], inline=True)
        embed.add_field(name="Отклонено", value=stats["denied"], inline=True)
        embed.add_field(name="Открыто", value=stats["open"], inline=True)

        weekly = stats["weekly"] if isinstance(stats, dict) else None
        if weekly:
            lines = [
                f"{row['date']}: {row['total_applications']} "
                f"(✅ {row['accepted']} / ❌ {row['denied']})"
                for row in weekly
            ]
            embed.add_field(name="По дням", value="\n".join(lines)[:1024], inline=False)

        await ctx.send(embed=embed)

    @commands.command(name=config.CMD_HISTORY)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def show_history(self, ctx, limit: int = 10):
        limit = min(max(limit, 1), 25)
        tickets = get_all_tickets(limit=limit, guild_id=ctx.guild.id)
        if not tickets:
            await ctx.send("Нет заявок в истории")
            return

        embed = discord.Embed(title="История заявок", color=discord.Color.blue())
        for t in tickets:
            emoji = "✅" if t["status"] == "accepted" else "❌" if t["status"] == "denied" else "🟡"
            user_text = "Удалённый пользователь" if t["user_id"] == 0 else f"<@{t['user_id']}>"
            embed.add_field(
                name=f"{emoji} {t['topic']}",
                value=f"От: {user_text}\n{t['created_at'][:10]}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name=config.CMD_DELETE_USER_DATA)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def delete_user_data(self, ctx: commands.Context, member: discord.Member):
        ticket_count = anonymize_user_tickets(ctx.guild.id, member.id)
        afk_counts = delete_afk_user_data(member.id, ctx.guild.id)
        await ctx.send(
            config.PRIVACY_DELETE_DONE.format(
                tickets=ticket_count,
                afk_users=afk_counts["afk_users"],
                afk_stats=afk_counts["afk_stats"],
                afk_cooldown=afk_counts["afk_cooldown"],
            )
        )


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
