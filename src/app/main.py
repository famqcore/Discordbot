import asyncio

import discord
from discord.ext import commands

import config
from afk.views import AfkMenuView
from database import init_afk_db, init_db
from tickets.commands import TicketTypeView
from tickets.views import FullTicketView
from utils.logcenter import LOG_KEY_ERRORS, send_to_log
from utils.logger import logger
from utils.mentions import DEFAULT_ALLOWED_MENTIONS
from utils.startup import check_startup

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class FamqCoreBot(commands.Bot):
    # инфраструктурная проверка выполняется один раз после первого READY;
    # startup_failed означает, что бот остановлен из-за ошибки конфигурации
    startup_checked = False
    startup_failed = False

    async def setup_hook(self):
        # setup_hook вызывается один раз при старте; on_ready — при каждом
        # переподключении, поэтому загрузка расширений живёт только здесь
        init_db()
        init_afk_db()
        await self.load_extension("tickets")
        await self.load_extension("afk")

        # persistent views: кнопки заявок и AFK продолжают работать
        # после перезапуска бота (custom_id прописаны у всех кнопок)
        self.add_view(TicketTypeView())
        self.add_view(FullTicketView())
        self.add_view(AfkMenuView())


bot = FamqCoreBot(
    command_prefix=config.CMD_PREFIX,
    intents=intents,
    # массовые пинги и пинги ролей запрещены глобально; адресные служебные
    # упоминания задаются явно в точках отправки (utils/mentions.py)
    allowed_mentions=DEFAULT_ALLOWED_MENTIONS,
)


@bot.event
async def on_ready():
    logger.info(f"Бот {bot.user} запущен")
    if not bot.startup_checked:
        bot.startup_checked = True
        # проверка заданных ID до обработки первой заявки (fail fast);
        # не блокирует on_ready, чтобы не терять heartbeat
        asyncio.create_task(check_startup(bot))


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return  # опечатки в чате молча игнорируем

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Эта команда работает только на сервере.")
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Не хватает аргумента `{error.param.name}`. Пример: `!{ctx.command} @user`")
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Неверный аргумент: {error}")
        return

    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Подождите {error.retry_after:.0f} сек. перед повтором команды.")
        return

    if isinstance(error, commands.MissingPermissions | commands.CheckFailure):
        await ctx.send("⛔ Недостаточно прав для этой команды.")
        return

    logger.error(f"Ошибка команды {ctx.command}: {error}")
    if ctx.guild is not None:
        embed = discord.Embed(title="🚨 Ошибка команды", color=discord.Color.red())
        embed.add_field(name="Команда", value=f"{ctx.command}", inline=True)
        embed.add_field(name="Автор", value=ctx.author.mention, inline=True)
        embed.add_field(name="Ошибка", value=str(error)[:500], inline=False)
        await send_to_log(ctx.guild, LOG_KEY_ERRORS, embed=embed)


if __name__ == "__main__":
    config_errors = config.validate()
    if config_errors:
        for err in config_errors:
            logger.critical(f"Конфиг: {err}")
        raise SystemExit(1)

    if not config.TOKEN:
        logger.critical("TOKEN не задан. Заполните .env по образцу .env.example")
        raise SystemExit(1)

    if config.ALLOW_NAME_FALLBACK:
        logger.warning(
            "Включён ALLOW_NAME_FALLBACK — НЕБЕЗОПАСНЫЙ режим разработки: "
            "объекты ищутся по имени, а проверки инфраструктуры не останавливают бота. "
            "Никогда не используйте в production."
        )

    bot.run(config.TOKEN)

    # check_startup остановил бота из-за ошибочной конфигурации
    if bot.startup_failed:
        raise SystemExit(1)
