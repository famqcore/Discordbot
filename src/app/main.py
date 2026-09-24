"""Точка входа: интенты, lifecycle бота и единый обработчик ошибок команд."""

from __future__ import annotations

import asyncio
import sqlite3

import discord
from discord.ext import commands

import config
from afk.views import AfkMenuView
from database import SchemaError, init_afk_db, init_db, schema_version, shutdown_db
from tickets.commands import TicketTypeView
from tickets.views import FullTicketView
from utils.errors import format_context, new_correlation_id
from utils.logcenter import LOG_KEY_ERRORS, send_to_log
from utils.logger import logger
from utils.mentions import DEFAULT_ALLOWED_MENTIONS
from utils.startup import check_startup

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

EXTENSIONS = ("tickets", "afk")


class FamqCoreBot(commands.Bot):
    # инфраструктурная проверка выполняется один раз после первого READY;
    # startup_failed означает, что бот остановлен из-за ошибки конфигурации
    startup_checked = False
    startup_failed = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._startup_task: asyncio.Task | None = None

    async def setup_hook(self):
        # setup_hook вызывается один раз при старте; on_ready — при каждом
        # переподключении, поэтому загрузка расширений живёт только здесь
        init_db()
        init_afk_db()
        logger.info(f"bot.startup outcome=schema_ready version=v{schema_version()}")

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        # persistent views: кнопки заявок и AFK продолжают работать
        # после перезапуска бота (custom_id прописаны у всех кнопок)
        self.add_view(TicketTypeView())
        self.add_view(FullTicketView())
        self.add_view(AfkMenuView())

    async def close(self):
        """Корректное завершение: задачи Cog отменяются, БД закрывается."""
        startup_task = self._startup_task
        if (
            startup_task is not None
            and not startup_task.done()
            and startup_task is not asyncio.current_task()
        ):
            startup_task.cancel()
        try:
            await super().close()
        finally:
            shutdown_db()
            logger.info("bot.shutdown outcome=ok")


bot = FamqCoreBot(
    command_prefix=config.CMD_PREFIX,
    intents=intents,
    # массовые пинги и пинги ролей запрещены глобально; адресные служебные
    # упоминания задаются явно в точках отправки (utils/mentions.py)
    allowed_mentions=DEFAULT_ALLOWED_MENTIONS,
)


@bot.event
async def on_ready():
    logger.info(f"bot.ready outcome=ok user={bot.user} guilds={len(bot.guilds)}")
    if not bot.startup_checked:
        bot.startup_checked = True
        # проверка заданных ID до обработки первой заявки (fail fast);
        # не блокирует on_ready, чтобы не терять heartbeat
        bot._startup_task = asyncio.create_task(check_startup(bot))


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

    # неожиданная ошибка: traceback и correlation id обязательны,
    # пользователь получает безопасный текст ровно один раз
    correlation_id = new_correlation_id()
    original = getattr(error, "original", error)
    context = format_context(
        command=getattr(ctx.command, "name", None),
        guild_id=getattr(ctx.guild, "id", None),
        channel_id=getattr(ctx.channel, "id", None),
        user_id=getattr(ctx.author, "id", None),
        correlation_id=correlation_id,
    )
    logger.exception(
        f"command.error outcome=error error_type={type(original).__name__} {context}",
        exc_info=original,
    )

    await ctx.send(f"⚠️ Команда завершилась ошибкой. Код для администратора: `{correlation_id}`")

    if ctx.guild is not None:
        embed = discord.Embed(title="🚨 Ошибка команды", color=discord.Color.red())
        embed.add_field(name="Команда", value=f"{ctx.command}", inline=True)
        embed.add_field(name="Код", value=f"`{correlation_id}`", inline=True)
        embed.add_field(name="Тип", value=type(original).__name__, inline=True)
        await send_to_log(ctx.guild, LOG_KEY_ERRORS, embed=embed)


def main() -> int:
    """Валидация конфигурации, запуск бота и корректный код выхода."""
    config_errors = config.validate(require_token=True)
    if config_errors:
        logger.critical("Конфигурация не прошла проверку, бот не запущен:")
        for err in config_errors:
            logger.critical(f"  • {err}")
        return 1

    if config.ALLOW_NAME_FALLBACK:
        logger.warning(
            "Включён ALLOW_NAME_FALLBACK — НЕБЕЗОПАСНЫЙ режим разработки: "
            "объекты ищутся по имени, а проверки инфраструктуры не останавливают бота. "
            "Никогда не используйте в production."
        )

    try:
        bot.run(config.TOKEN, log_handler=None)
    except discord.LoginFailure:
        logger.critical("Discord отклонил TOKEN. Проверьте значение в .env")
        return 1
    except SchemaError as error:
        logger.critical(f"Схема базы данных: {error}")
        return 1
    except sqlite3.Error as error:
        logger.critical(f"База данных недоступна: {type(error).__name__}")
        return 1
    finally:
        shutdown_db()

    # check_startup остановил бота из-за ошибочной конфигурации
    return 1 if bot.startup_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
