import re
from datetime import datetime, timedelta

import discord

import config
from utils.logcenter import LOG_KEY_AFK, send_to_log
from utils.mentions import escape_user_text, mentions_for
from utils.ratelimit import retry_after


def parse_return_time(text: str):
    text = text.lower().strip()
    now = datetime.now()

    # Формат ЧЧ:ММ — strptime сам отвалидирует диапазоны (минуты > 59 отвалятся)
    try:
        dt = datetime.strptime(text, "%H:%M")
        target = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    except ValueError:
        pass

    # Число с единицей: «2 часа», «30 мин», «3 ч», «через 15 м»
    match = re.search(r"(\d+)\s*(час(?:а|ов)?|мин(?:ут[аы]?)?|ч|м)\b", text)
    if not match:
        return None
    num = int(match.group(1))
    unit = match.group(2)

    if unit.startswith("ч"):
        return now + timedelta(hours=num)
    return now + timedelta(minutes=num)


def build_afk_embed(guild: discord.Guild):
    from .models import get_all_afk

    rows = get_all_afk(guild.id)

    embed = discord.Embed(
        title=config.AFK_MENU_TITLE,
        color=discord.Color.red(),
    )
    embed.add_field(name=config.AFK_MENU_TOTAL, value=f"{len(rows)} человек", inline=False)

    lines = []
    overflow = 0
    total_len = 0
    for idx, row in enumerate(rows, 1):
        member = guild.get_member(row["user_id"])
        name = member.mention if member else f"<@{row['user_id']}>"
        reason = row.get("afk_reason") or config.AFK_REASON_DEFAULT
        afk_since = datetime.fromisoformat(row["afk_since"])
        since_str = afk_since.strftime("%H:%M")
        return_str = "—"
        if row.get("estimated_return"):
            try:
                ret = datetime.fromisoformat(row["estimated_return"])
                return_str = ret.strftime("%H:%M")
            except Exception:
                return_str = str(row["estimated_return"])[:20]
        line = f"{idx}) {name} | Причина: {reason}    Ушел: {since_str} | Вернется: {return_str}"
        # запас под лимит description (4096), иначе длинный список ломает эмбед
        if total_len + len(line) > 3900:
            overflow += 1
            continue
        total_len += len(line) + 1
        lines.append(line)

    if lines:
        embed.description = "\n".join(lines)
        if overflow:
            embed.description += f"\n…и ещё {overflow}"
    else:
        embed.description = config.AFK_MENU_NO_AFK

    return embed


class AfkReturnView(discord.ui.View):
    def __init__(self, member: discord.Member, guild_id: int, duration_text: str):
        super().__init__(timeout=60)
        self.member = member
        self.guild_id = guild_id
        self.duration_text = duration_text

    @discord.ui.button(label=config.AFK_BUTTON_RETURN, style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(config.AFK_INVALID_USER, ephemeral=True)
            return

        from .models import get_afk_user, remove_afk, remove_afk_nickname

        row = get_afk_user(self.member.id, self.guild_id)
        duration = remove_afk(self.member.id, self.guild_id)
        if duration is None:
            await interaction.response.send_message(config.AFK_RETURN_ERROR, ephemeral=True)
            return

        await remove_afk_nickname(self.member, row.get("original_nick") if row else None)
        await interaction.response.edit_message(
            content=f"{config.AFK_RETURN_SUCCESS} Отсутствовали: {self.duration_text}.",
            embed=None,
            view=None,
        )

        if interaction.guild is not None:
            log_embed = discord.Embed(
                title=config.AFK_LOG_REMOVED_TITLE,
                color=discord.Color.green(),
            )
            log_embed.add_field(name="Пользователь", value=self.member.mention, inline=True)
            log_embed.add_field(name="Отсутствовал", value=self.duration_text, inline=True)
            log_embed.add_field(name="Кто снял", value="сам", inline=True)
            await send_to_log(interaction.guild, LOG_KEY_AFK, embed=log_embed)

        self.stop()

    @discord.ui.button(label=config.AFK_BUTTON_STAY, style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(config.AFK_INVALID_USER, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=config.AFK_RETURN_STAY, embed=None, view=None
        )
        self.stop()


class AfkSetModal(discord.ui.Modal, title=config.AFK_MODAL_TITLE):
    reason = discord.ui.TextInput(
        label=config.AFK_MODAL_REASON_LABEL,
        placeholder=config.AFK_MODAL_REASON_PLACEHOLDER,
        required=False,
        max_length=100,
    )
    duration = discord.ui.TextInput(
        label=config.AFK_MODAL_DURATION_LABEL,
        placeholder=config.AFK_MODAL_DURATION_PLACEHOLDER,
        required=True,
        max_length=50,
    )

    def __init__(self, member: discord.Member, guild_id: int, guild: discord.Guild):
        super().__init__()
        self.member = member
        self.guild_id = guild_id
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        from .models import add_afk_nickname, set_afk

        reason = self.reason.value or config.AFK_REASON_DEFAULT
        parsed = parse_return_time(self.duration.value)
        if parsed is None:
            await interaction.response.send_message(
                "❌ Не удалось распознать время. Попробуйте снова.",
                ephemeral=True,
            )
            return

        estimated_return = parsed.isoformat()
        set_afk(self.member.id, self.guild_id, reason, estimated_return, self.member.nick)
        await add_afk_nickname(self.member)
        await interaction.response.send_message(
            f"🔴 Вы в AFK.\nПричина: {escape_user_text(reason)}\n"
            f"Вернётесь: <t:{int(parsed.timestamp())}:R>",
            ephemeral=True,
            allowed_mentions=mentions_for(),
        )

        if self.guild is not None:
            log_embed = discord.Embed(
                title=config.AFK_LOG_SET_TITLE,
                color=discord.Color.red(),
            )
            log_embed.add_field(name="Пользователь", value=self.member.mention, inline=True)
            log_embed.add_field(
                name="Вернётся", value=f"<t:{int(parsed.timestamp())}:f>", inline=True
            )
            log_embed.add_field(name="Причина", value=reason, inline=False)
            await send_to_log(self.guild, LOG_KEY_AFK, embed=log_embed)


class AfkMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_guild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(config.AFK_GUILD_ONLY, ephemeral=True)
            return False
        return True

    async def _check_button_cooldown(self, interaction: discord.Interaction, action: str) -> bool:
        guild_id = getattr(interaction, "guild_id", None)
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        if not isinstance(guild_id, int) or not isinstance(user_id, int):
            return True
        wait = retry_after(
            ("afk_button", action, guild_id, user_id), config.AFK_COMMAND_COOLDOWN_SECONDS
        )
        if wait:
            await interaction.response.send_message(
                f"⏳ Подождите {wait} сек. перед повторным нажатием.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label=config.AFK_BUTTON_LEAVE,
        style=discord.ButtonStyle.danger,
        custom_id="afk_leave",
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_guild(interaction):
            return
        if not await self._check_button_cooldown(interaction, "leave"):
            return
        modal = AfkSetModal(interaction.user, interaction.guild_id, interaction.guild)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label=config.AFK_BUTTON_RETURN,
        style=discord.ButtonStyle.success,
        custom_id="afk_return",
    )
    async def return_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_guild(interaction):
            return
        if not await self._check_button_cooldown(interaction, "return"):
            return

        from .models import format_duration, get_afk_user

        row = get_afk_user(interaction.user.id, interaction.guild_id)
        if not row:
            await interaction.response.send_message(config.AFK_NOT_AFK, ephemeral=True)
            return

        afk_since = datetime.fromisoformat(row["afk_since"])
        duration = int((datetime.now() - afk_since).total_seconds())
        duration_text = format_duration(duration)

        embed = discord.Embed(
            title=config.AFK_RETURN_MODAL_TITLE,
            description=f"{config.AFK_RETURN_CONFIRM}\n\n**{config.AFK_RETURN_DURATION_LABEL}:** {duration_text}",
            color=discord.Color.orange(),
        )
        view = AfkReturnView(interaction.user, interaction.guild_id, duration_text)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label=config.AFK_BUTTON_REFRESH,
        style=discord.ButtonStyle.primary,
        custom_id="afk_refresh",
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_guild(interaction):
            return
        if not await self._check_button_cooldown(interaction, "refresh"):
            return
        embed = build_afk_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)
