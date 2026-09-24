"""Лог-центр: один приватный канал, внутри — ветки по категориям логов.

Принцип fail closed: транскрипты и метаданные заявок отправляются только
в точку, прошедшую проверку приватности.

- Настроенный LOG_CHANNEL_ID / LOG_THREAD_*_ID (внешний объект) проверяется
  на тип, принадлежность серверу и приватность: @everyone без просмотра,
  доступ только у стафф-ролей и бота. Не прошёл проверку — данных туда нет,
  никакого fallback в корень канала или «куда-нибудь».
- Если ID не заданы, бот создаёт свой управляемый приватный лог-центр
  (закрыт для @everyone, открыт боту и STAFF_ROLE_IDS) и запоминает ID
  созданных объектов в bot_state. Управляемый канал принадлежит боту,
  поэтому дрейф прав чинится принудительно и заметно.

Устойчивость к архивации: ветка ищется не только среди
активных (``channel.threads``), но и среди архивированных — через
``archived_threads()`` и ``fetch_channel``. Найденная архивированная
ветка разархивируется, поэтому история одной категории не распадается
на дубли после автоархивации.

По умолчанию send_to_log не бросает исключений наружу (логирование не
должно ронять основную логику), но критичный вызывающий код может запросить
``raise_http_errors`` и сам решить, нужно ли откатить операцию. Каждая
потеря аудита увеличивает счётчик ``delivery_stats()`` — длительная
деградация видна оператору.
"""

from __future__ import annotations

import discord

import config
from database import state_db
from utils.logger import logger
from utils.mentions import mentions_for

# Ключи веток (совпадают с config.LOG_KEY_*)
LOG_KEY_TICKETS = config.LOG_KEY_TICKETS
LOG_KEY_DECISIONS = config.LOG_KEY_DECISIONS
LOG_KEY_AFK = config.LOG_KEY_AFK
LOG_KEY_CALLS = config.LOG_KEY_CALLS
LOG_KEY_STATS = config.LOG_KEY_STATS
LOG_KEY_ERRORS = config.LOG_KEY_ERRORS
LOG_KEY_AUDIT = config.LOG_KEY_AUDIT

MAX_AUTO_ARCHIVE = 10080  # неделя — максимум у Discord
ARCHIVED_SCAN_LIMIT = 100

# Счётчики доставки логов: видимость деградации аудита без раскрытия данных.
_DELIVERY = {"sent": 0, "rejected": 0, "failed": 0}
# Порог подряд идущих неудач, после которого пишем CRITICAL
DEGRADED_ALERT_THRESHOLD = 5
_consecutive_failures = 0


def delivery_stats() -> dict[str, int]:
    """Счётчики отправок лог-центра (sent / rejected / failed)."""
    return dict(_DELIVERY)


def reset_delivery_stats() -> None:
    global _consecutive_failures
    for key in _DELIVERY:
        _DELIVERY[key] = 0
    _consecutive_failures = 0


def _record_delivery(outcome: str, key: str, detail: str = "") -> None:
    """Фиксирует исход отправки и поднимает тревогу при длительной деградации."""
    global _consecutive_failures
    _DELIVERY[outcome] = _DELIVERY.get(outcome, 0) + 1
    if outcome == "sent":
        _consecutive_failures = 0
        return
    _consecutive_failures += 1
    message = f"logcenter outcome={outcome} key={key} consecutive_failures={_consecutive_failures}"
    if detail:
        message = f"{message} detail={detail}"
    if _consecutive_failures >= DEGRADED_ALERT_THRESHOLD:
        logger.critical(
            f"{message} — аудит не пишется подряд "
            f"{_consecutive_failures} раз, проверьте конфигурацию лог-центра"
        )
    else:
        logger.error(message)


def _private_overwrites(guild):
    """Права для лог-канала: приватный, доступен боту и стафф-ролям."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


def audit_channel_privacy(guild, channel) -> list[str]:
    """Аудит приватности лог-канала. Пустой список — канал безопасен.

    Транскрипты нельзя хранить в канале, который виден шире, чем
    утверждённый круг: бот + STAFF_ROLE_IDS. Роли с правом Administrator
    видят любой канал на уровне Discord — это граница доверия сервера,
    проверка их не охватывает.
    """
    if not isinstance(channel, discord.TextChannel):
        return [f"объект {getattr(channel, 'id', channel)} не является текстовым каналом сервера"]
    if getattr(getattr(channel, "guild", None), "id", None) != getattr(guild, "id", None):
        return [f"канал {channel.id} принадлежит другому серверу"]

    problems = []
    if channel.permissions_for(guild.default_role).view_channel:
        problems.append("@everyone видит канал — логи с транскриптами должны быть приватными")

    bot_user_id = getattr(getattr(guild, "me", None), "id", None)
    bot_role_ids = {getattr(role, "id", None) for role in getattr(guild.me, "roles", []) or []}
    approved_role_ids = set(config.STAFF_ROLE_IDS)

    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is not True:
            continue
        if isinstance(target, discord.Role):
            if target.is_default():
                continue  # уже разобран выше через permissions_for
            if target.id in approved_role_ids or target.id in bot_role_ids:
                continue
            problems.append(
                f"роль «{getattr(target, 'name', target.id)}» видит канал, "
                "но не входит в утверждённые STAFF_ROLE_IDS"
            )
        else:
            # персональный overwrite участника: допустим только для самого бота
            if getattr(target, "id", None) == bot_user_id:
                continue
            problems.append(
                f"участник {getattr(target, 'id', target)} имеет персональный доступ к каналу"
            )
    return problems


async def _audit_thread(guild, channel, thread) -> list[str]:
    """Проверка ветки лог-центра: тип, сервер, родитель, разархивирование.

    Тип проверяется строго: произвольный «что-нибудь с методом send»
    destination не принимается — только ``discord.Thread`` в настроенном
    лог-канале этого сервера.
    """
    if not isinstance(thread, discord.Thread):
        return [
            f"объект {getattr(thread, 'id', thread)} не является веткой "
            f"(тип {type(thread).__name__})"
        ]
    if getattr(getattr(thread, "guild", None), "id", None) != getattr(guild, "id", None):
        return [f"ветка {thread.id} принадлежит другому серверу"]
    if getattr(thread, "parent_id", None) != getattr(channel, "id", None):
        return [f"ветка {thread.id} находится не в настроенном лог-канале"]
    if getattr(thread, "locked", False):
        return [f"ветка {thread.id} заблокирована (locked), писать в неё нельзя"]
    if getattr(thread, "archived", False):
        try:
            await thread.edit(archived=False)
            logger.info(
                f"logcenter outcome=unarchived thread_id={thread.id} "
                f"name={getattr(thread, 'name', '?')}"
            )
        except discord.Forbidden:
            return [f"ветка {thread.id} архивирована, у бота нет прав её разархивировать"]
        except discord.HTTPException as error:
            return [
                f"ветка {thread.id} архивирована, разархивировать не удалось "
                f"({type(error).__name__})"
            ]
    return []


async def _fetch_guild_channel(guild, channel_id):
    """Канал/ветка из кэша сервера или API; None, если не существует."""
    channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
    if channel is not None:
        return channel
    try:
        return await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    except discord.HTTPException as error:
        logger.warning(
            f"logcenter outcome=fetch_failed channel_id={channel_id} "
            f"error_type={type(error).__name__}"
        )
        return None


async def _find_thread_by_name(channel, name: str):
    """Ищет ветку по имени среди активных И архивированных.

    Без просмотра архива бот после автоархивации создал бы вторую ветку
    с тем же именем, и история категории разошлась бы на две.
    """
    for thread in getattr(channel, "threads", []) or []:
        if getattr(thread, "name", None) == name:
            return thread

    archived = getattr(channel, "archived_threads", None)
    if archived is None:
        return None
    try:
        async for thread in archived(limit=ARCHIVED_SCAN_LIMIT):
            if getattr(thread, "name", None) == name:
                return thread
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.warning(
            f"logcenter outcome=archive_scan_failed channel_id={getattr(channel, 'id', None)} "
            f"error_type={type(error).__name__}"
        )
    return None


async def _repair_managed_channel(guild, channel) -> bool:
    """Управляемый канал принадлежит боту: права приводятся к приватным."""
    if not isinstance(channel, discord.TextChannel):
        return False
    if getattr(getattr(channel, "guild", None), "id", None) != getattr(guild, "id", None):
        return False
    try:
        await channel.edit(overwrites=_private_overwrites(guild))
    except Exception as e:
        logger.error(f"logcenter: не удалось восстановить права лог-канала: {e}")
        return False
    logger.warning(
        f"logcenter: права управляемого лог-канала {channel.id} приведены к приватным "
        "(обнаружен дрейф конфигурации сервера)"
    )
    return True


async def _resolve_log_channel(guild):
    """Лог-канал: внешний по LOG_CHANNEL_ID (строгая проверка) или управляемый."""
    if config.LOG_CHANNEL_ID:
        channel = await _fetch_guild_channel(guild, config.LOG_CHANNEL_ID)
        if channel is None:
            logger.error(f"logcenter: LOG_CHANNEL_ID={config.LOG_CHANNEL_ID} не найден")
            return None
        problems = audit_channel_privacy(guild, channel)
        if problems:
            logger.error(
                f"logcenter: настроенный канал {config.LOG_CHANNEL_ID} небезопасен: "
                f"{'; '.join(problems)}"
            )
            return None
        return channel

    # Управляемый канал: ищем сохранённый ID, чиним права при дрейфе,
    # отсутствующий — создаём приватным.
    state_key = f"log_channel:{getattr(guild, 'id', 0)}"
    stored = await state_db.async_get_state(state_key)
    if stored and stored.isdigit():
        channel = await _fetch_guild_channel(guild, int(stored))
        if channel is not None:
            if not audit_channel_privacy(guild, channel):
                return channel
            if await _repair_managed_channel(guild, channel):
                return channel
            return None
        await state_db.async_delete_state(state_key)

    channel = await guild.create_text_channel(
        config.LOG_CHANNEL_NAME, overwrites=_private_overwrites(guild)
    )
    await state_db.async_set_state(state_key, str(channel.id))
    logger.info(f"Создал приватный лог-канал «{config.LOG_CHANNEL_NAME}»")
    return channel


async def _resolve_thread(guild, key: str):
    """Ветка логов: внешняя по LOG_THREAD_*_ID (строгая проверка) или управляемая."""
    channel = await _resolve_log_channel(guild)
    if channel is None or not isinstance(channel, discord.TextChannel):
        return None

    thread_id = config.LOG_THREAD_IDS.get(key)
    name = config.LOG_THREAD_NAMES.get(key, key)
    env_name = config.LOG_THREAD_ENV_NAMES.get(key, key)

    if thread_id:
        thread = await _fetch_guild_channel(guild, thread_id)
        if thread is None:
            logger.error(f"logcenter: {env_name}={thread_id} не найден")
            return None
        problems = await _audit_thread(guild, channel, thread)
        if problems:
            logger.error(
                f"logcenter: {env_name}={thread_id} не прошёл проверку: {'; '.join(problems)}"
            )
            return None
        return thread

    state_key = f"log_thread:{getattr(guild, 'id', 0)}:{key}"
    stored = await state_db.async_get_state(state_key)
    if stored and stored.isdigit():
        thread = await _fetch_guild_channel(guild, int(stored))
        if thread is not None:
            problems = await _audit_thread(guild, channel, thread)
            if not problems:
                return thread
            logger.warning(
                f"logcenter: управляемая ветка «{key}» не прошла проверку "
                f"({'; '.join(problems)}), пересоздаю"
            )
        await state_db.async_delete_state(state_key)

    # ID не сохранён (первый запуск после обновления или потеря bot_state):
    # ищем существующую ветку по имени, включая архивированные, чтобы не
    # расколоть историю категории на дубли
    existing = await _find_thread_by_name(channel, name)
    if existing is not None:
        problems = await _audit_thread(guild, channel, existing)
        if not problems:
            await state_db.async_set_state(state_key, str(existing.id))
            logger.info(
                f"logcenter outcome=reused_thread key={key} thread_id={existing.id} "
                "(ветка найдена по имени, в том числе в архиве)"
            )
            return existing
        logger.warning(
            f"logcenter: найденная по имени ветка «{name}» непригодна "
            f"({'; '.join(problems)}), создаю новую"
        )

    thread = await channel.create_thread(
        name=name,
        type=discord.ChannelType.public_thread,
        auto_archive_duration=MAX_AUTO_ARCHIVE,
    )
    await state_db.async_set_state(state_key, str(thread.id))
    logger.info(f"Создал ветку логов «{name}» в канале «{config.LOG_CHANNEL_NAME}»")
    return thread


async def validate_log_center_config(guild) -> list[str]:
    """Проверка настроенного лог-центра на старте. Пустой список — всё ок.

    Ненастроенный лог-центр — не ошибка: бот создаст управляемый приватный
    при первой записи.
    """
    if config.LOG_CHANNEL_ID is None:
        return []

    channel = await _fetch_guild_channel(guild, config.LOG_CHANNEL_ID)
    if channel is None:
        return [f"LOG_CHANNEL_ID={config.LOG_CHANNEL_ID}: канал не найден"]

    problems = [
        f"LOG_CHANNEL_ID={config.LOG_CHANNEL_ID}: {p}"
        for p in audit_channel_privacy(guild, channel)
    ]
    if not isinstance(channel, discord.TextChannel):
        return problems

    for key, thread_id in config.LOG_THREAD_IDS.items():
        if thread_id is None:
            continue
        env_name = config.LOG_THREAD_ENV_NAMES.get(key, key)
        thread = await _fetch_guild_channel(guild, thread_id)
        if thread is None:
            problems.append(f"{env_name}={thread_id}: ветка не найдена")
            continue
        problems.extend(
            f"{env_name}={thread_id}: {p}" for p in await _audit_thread(guild, channel, thread)
        )
    return problems


async def send_to_log(
    guild,
    key: str,
    content: str | None = None,
    embed=None,
    files=None,
    *,
    raise_http_errors: bool = False,
) -> discord.Message | None:
    """Отправляет сообщение в проверенную ветку лог-центра.

    Возвращает отправленное сообщение (нужно удалению данных, чтобы потом
    стереть транскрипты) или None, если точка отправки не прошла проверку
    приватности/конфигурации. ``raise_http_errors`` нужен транзакциям, для
    которых отсутствие аудита требует отката; счётчики доставки обновляются
    в обоих режимах.
    """
    if guild is None:
        return None

    try:
        destination = await _resolve_thread(guild, key)
    except (discord.Forbidden, discord.HTTPException) as error:
        _record_delivery("failed", key, f"resolve_{type(error).__name__}")
        if raise_http_errors:
            raise
        return None
    except Exception as error:  # noqa: BLE001 - логирование не должно ронять бота
        logger.exception(f"logcenter outcome=resolve_error key={key}")
        _record_delivery("failed", key, type(error).__name__)
        return None

    if destination is None:
        _record_delivery(
            "rejected",
            key,
            "fail_closed: лог-центр не прошёл проверку приватности/конфигурации",
        )
        return None

    try:
        message = await destination.send(
            content=content, embed=embed, files=files, allowed_mentions=mentions_for()
        )
    except discord.Forbidden:
        _record_delivery("failed", key, "нет прав на отправку в ветку лог-центра")
        if raise_http_errors:
            raise
        return None
    except discord.HTTPException as error:
        _record_delivery("failed", key, f"http_{getattr(error, 'status', '?')}")
        if raise_http_errors:
            raise
        return None
    except Exception as error:  # noqa: BLE001 - логирование не должно ронять бота
        logger.exception(f"logcenter outcome=send_error key={key}")
        _record_delivery("failed", key, type(error).__name__)
        return None

    _DELIVERY["sent"] += 1
    global _consecutive_failures
    _consecutive_failures = 0
    return message


async def delete_log_messages(guild, refs) -> int:
    """Удаляет сообщения лог-центра по парам (thread_id, message_id).

    Используется удалением персональных данных и ретенцией, чтобы стереть
    сохранённые транскрипты. Возвращает число удалённых сообщений.
    """
    deleted = 0
    for thread_id, message_id in refs:
        thread = await _fetch_guild_channel(guild, thread_id)
        if thread is None:
            logger.warning(
                f"logcenter outcome=delete_skipped thread_id={thread_id} "
                f"message_id={message_id} reason=thread_unavailable"
            )
            continue
        if isinstance(thread, discord.Thread) and getattr(thread, "archived", False):
            # архивированную ветку сначала открываем, иначе удаление не пройдёт
            try:
                await thread.edit(archived=False)
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.warning(
                    f"logcenter outcome=delete_skipped thread_id={thread_id} "
                    f"message_id={message_id} reason=unarchive_failed "
                    f"error_type={type(error).__name__}"
                )
                continue
        try:
            message = await thread.fetch_message(message_id)
            await message.delete()
            deleted += 1
        except discord.NotFound:
            # сообщение уже удалено — цель достигнута
            deleted += 1
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning(
                f"logcenter outcome=delete_failed thread_id={thread_id} "
                f"message_id={message_id} error_type={type(error).__name__}"
            )
    return deleted
