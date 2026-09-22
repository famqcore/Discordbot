"""Проверка инфраструктуры сервера на старте.

Правило fail fast (production): заданный, но не найденный ID — это ошибка
конфигурации, а не повод искать «что-нибудь похожее» по имени. Бот с такой
конфигурацией останавливается с понятным списком проблем до обработки
первой заявки.

В небезопасном режиме разработки (ALLOW_NAME_FALLBACK=true) те же проблемы
выводятся предупреждениями, и бот продолжает работу.
"""

import discord

import config
from utils.logcenter import validate_log_center_config
from utils.logger import logger


def _access_role_specs() -> tuple:
    """Роли, влияющие на доступ к тикетам и логам (читаются из config при вызове)."""
    return (
        ("ROLE_RECRUITER_ID", config.ROLE_RECRUITER_ID),
        ("ROLE_OWNER_ID", config.ROLE_OWNER_ID),
        ("ROLE_DEP_OWNER_ID", config.ROLE_DEP_OWNER_ID),
        ("ROLE_ADMIN_ID", config.ROLE_ADMIN_ID),
        ("ROLE_SUPPORT_ID", config.ROLE_SUPPORT_ID),
        ("ROLE_APPLIED_ID", config.ROLE_APPLIED_ID),
    )


async def validate_guild_infrastructure(guild) -> list[str]:
    """Проверяет все заданные ID одного сервера. Пустой список — всё ок."""
    problems = []

    for env_name, role_id in _access_role_specs():
        if role_id and guild.get_role(role_id) is None:
            problems.append(f"{env_name}={role_id}: роль не найдена на сервере")

    if config.TICKETS_CATEGORY_ID:
        category = guild.get_channel(config.TICKETS_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            problems.append(
                f"TICKETS_CATEGORY_ID={config.TICKETS_CATEGORY_ID}: "
                "не найдена или не является категорией"
            )

    for channel_id in config.VOICE_CHANNEL_IDS:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            problems.append(
                f"VOICE_CHANNEL_IDS: {channel_id}: не найден или не является голосовым каналом"
            )

    problems.extend(await validate_log_center_config(guild))
    return problems


async def check_startup(bot) -> bool:
    """Разовая проверка всех серверов после подключения.

    Возвращает True, если конфигурация в порядке. В production при проблемах
    пишет их в лог как CRITICAL и корректно останавливает бота (bot.close(),
    точку выхода обрабатывает main).
    """
    all_problems = []
    for guild in bot.guilds:
        try:
            problems = await validate_guild_infrastructure(guild)
        except Exception as e:
            problems = [f"не удалось проверить конфигурацию сервера: {e}"]
        guild_name = getattr(guild, "name", "?")
        guild_id = getattr(guild, "id", "?")
        all_problems.extend(f"[{guild_name} ({guild_id})] {p}" for p in problems)

    if not all_problems:
        logger.info("Проверка инфраструктуры: конфигурация серверов в порядке")
        return True

    if config.ALLOW_NAME_FALLBACK:
        for problem in all_problems:
            logger.warning(f"Инфраструктура (небезопасный режим разработки): {problem}")
        return True

    for problem in all_problems:
        logger.critical(f"Инфраструктура: {problem}")
    logger.critical(
        "Бот остановлен: заданные ID не соответствуют серверу. "
        "Исправьте .env (поиск по имени в production отключён) — "
        "или, только для локальной разработки, включите ALLOW_NAME_FALLBACK=true."
    )
    bot.startup_failed = True
    await bot.close()
    return False
