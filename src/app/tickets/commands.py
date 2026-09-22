"""Префиксные команды заявок и панель подачи."""

from __future__ import annotations

import discord
from discord.ext import commands

import config
from database.schema import STATUS_ACCEPTED, STATUS_DENIED
from database.tickets_db import get_all_tickets, get_open_ticket_for_user, get_stats
from utils.errors import InteractionErrorBoundary, new_correlation_id
from utils.logger import logger
from utils.mentions import escape_user_text, mentions_for
from utils.ratelimit import retry_after

from .create_ticket import TicketModal
from .erasure import audit_erasure, erase_user_data


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
            # проверка активной заявки и кулдаун до открытия формы:
            # пользователь не тратит время на анкету, которую нельзя подать
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
        async with InteractionErrorBoundary(interaction, "ticket.form_open", ticket_type="rp"):
            await self._send_modal_or_existing_ticket(
                interaction,
                config.TICKET_RP_TITLE,
                "rp",
                config.RP_FIELDS,
            )

    async def capt_callback(self, interaction: discord.Interaction):
        async with InteractionErrorBoundary(interaction, "ticket.form_open", ticket_type="capt"):
            await self._send_modal_or_existing_ticket(
                interaction,
                config.TICKET_CAPT_TITLE,
                "capt",
                config.CAPT_FIELDS,
            )


class DeleteUserDataConfirmView(discord.ui.View):
    """Подтверждение необратимого удаления персональных данных.

    Реагирует только на администратора, вызвавшего команду.
    """

    def __init__(self, admin_id: int, member: discord.Member):
        super().__init__(timeout=60)
        self.admin_id = admin_id
        self.member = member

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(config.PRIVACY_DELETE_NOT_ADMIN, ephemeral=True)
            return False
        return True

    @discord.ui.button(label=config.PRIVACY_DELETE_CONFIRM_BUTTON, style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin(interaction):
            return
        # удаление — серия запросов к Discord, отвечаем отложенно
        await interaction.response.defer()
        try:
            counts = await erase_user_data(interaction.guild, self.member)
            await audit_erasure(interaction.guild, interaction.user, self.member, counts)
            text = config.PRIVACY_DELETE_DONE.format(**counts)
        except Exception as error:  # noqa: BLE001 - границы административной операции
            correlation_id = new_correlation_id()
            logger.exception(
                f"erasure outcome=error error_type={type(error).__name__} "
                f"subject_id={self.member.id} guild_id={getattr(interaction.guild, 'id', None)} "
                f"correlation_id={correlation_id}"
            )
            text = f"{config.PRIVACY_DELETE_FAILED}\nКод для администратора: `{correlation_id}`"
        await interaction.edit_original_response(
            content=text, view=None, allowed_mentions=mentions_for()
        )
        self.stop()

    @discord.ui.button(
        label=config.PRIVACY_DELETE_CANCEL_BUTTON, style=discord.ButtonStyle.secondary
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_admin(interaction):
            return
        await interaction.response.edit_message(content=config.PRIVACY_DELETE_CANCELLED, view=None)
        self.stop()


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

        await ctx.send(embed=embed, allowed_mentions=mentions_for())

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
            status = t["status"]
            emoji = "✅" if status == STATUS_ACCEPTED else "❌" if status == STATUS_DENIED else "🟡"
            user_text = "Удалённый пользователь" if t["user_id"] == 0 else f"<@{t['user_id']}>"
            embed.add_field(
                name=f"{emoji} {escape_user_text(t['topic'])}"[:256],
                value=f"От: {user_text}\n{t['created_at'][:10]}",
                inline=False,
            )
        await ctx.send(embed=embed, allowed_mentions=mentions_for())

    @commands.command(name=config.CMD_DELETE_USER_DATA)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.cooldown(2, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def delete_user_data(self, ctx: commands.Context, member: discord.Member):
        """Необратимое удаление данных пользователя (требует подтверждения)."""
        view = DeleteUserDataConfirmView(ctx.author.id, member)
        await ctx.send(
            config.PRIVACY_DELETE_CONFIRM.format(member=f"{member.mention} (ID {member.id})"),
            view=view,
            allowed_mentions=mentions_for(),
        )


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
