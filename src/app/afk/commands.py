from datetime import datetime

import discord
from discord.ext import commands

import config
from utils.logcenter import LOG_KEY_AFK, send_to_log
from utils.permissions import is_staff

from .models import (
    format_duration,
    get_afk_user,
    get_user_stats,
    remove_afk,
    remove_afk_nickname,
)
from .views import AfkMenuView, build_afk_embed


class AfkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="afk")
    @commands.guild_only()
    @commands.cooldown(1, config.AFK_COMMAND_COOLDOWN_SECONDS, commands.BucketType.user)
    async def afk_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title=config.AFK_EMBED_TITLE,
            description=config.AFK_EMBED_DESCRIPTION,
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed, view=AfkMenuView())

    @commands.command(name="afk_list")
    @commands.guild_only()
    @commands.cooldown(1, config.AFK_LIST_COOLDOWN_SECONDS, commands.BucketType.user)
    async def afk_list_command(self, ctx: commands.Context):
        await ctx.send(embed=build_afk_embed(ctx.guild))

    @commands.command(name="afk_check")
    @commands.guild_only()
    @commands.cooldown(3, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def afk_check_command(self, ctx: commands.Context, member: discord.Member):
        row = get_afk_user(member.id, ctx.guild.id)
        if not row:
            embed = discord.Embed(
                title=member.display_name,
                description=config.AFK_CHECKED_NOT_AFK,
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            return

        afk_since = datetime.fromisoformat(row["afk_since"])
        duration = int((datetime.now() - afk_since).total_seconds())
        reason = row.get("afk_reason") or "Отошёл"

        embed = discord.Embed(
            title=member.display_name,
            color=discord.Color.orange(),
        )
        embed.add_field(name=config.AFK_FIELD_STATUS, value=config.AFK_AFK_STATUS, inline=False)
        embed.add_field(name=config.AFK_FIELD_REASON, value=reason, inline=False)
        embed.add_field(name=config.AFK_FIELD_LEFT, value=afk_since.strftime("%H:%M"), inline=True)
        embed.add_field(
            name=config.AFK_FIELD_DURATION, value=format_duration(duration), inline=True
        )

        await ctx.send(embed=embed)

    @commands.command(name="afk_stats")
    @commands.guild_only()
    @commands.cooldown(3, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def afk_stats_command(self, ctx: commands.Context, member: discord.Member):
        stats = get_user_stats(member.id, ctx.guild.id)
        if not stats:
            embed = discord.Embed(
                title=member.display_name,
                description=config.AFK_STATS_NO_DATA,
                color=discord.Color.blue(),
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title=f"{config.AFK_STATS_TITLE}: {member.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name=config.AFK_STATS_TOTAL, value=stats["total_afk_count"], inline=True)
        embed.add_field(
            name=config.AFK_STATS_TOTAL_TIME,
            value=format_duration(stats["total_afk_seconds"]),
            inline=True,
        )
        embed.add_field(
            name=config.AFK_STATS_LONGEST,
            value=format_duration(stats["longest_afk_seconds"]),
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.command(name=config.CMD_AFK_REMOVE)
    @commands.guild_only()
    @commands.cooldown(3, config.AFK_LOOKUP_COOLDOWN_SECONDS, commands.BucketType.user)
    async def afk_remove_command(self, ctx: commands.Context, member: discord.Member):
        """Принудительно снять AFK с пользователя (только модераторы)."""
        if not is_staff(ctx.author):
            await ctx.send(config.AFK_NO_PERMISSION)
            return

        row = get_afk_user(member.id, ctx.guild.id)
        duration = remove_afk(member.id, ctx.guild.id)
        if duration is None:
            await ctx.send(config.AFK_CHECKED_NOT_AFK)
            return

        await remove_afk_nickname(member, row.get("original_nick") if row else None)

        embed = discord.Embed(
            title=config.AFK_LOG_REMOVED_TITLE,
            color=discord.Color.green(),
        )
        embed.add_field(name="Пользователь", value=member.mention, inline=True)
        embed.add_field(name="Отсутствовал", value=format_duration(duration), inline=True)
        embed.add_field(name="Снял", value=ctx.author.mention, inline=True)

        await ctx.send(embed=embed)
        await send_to_log(ctx.guild, LOG_KEY_AFK, embed=embed)
