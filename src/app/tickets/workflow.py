"""Общий сценарий терминального действия над заявкой.

Порядок шагов зафиксирован и одинаков для «Принять», «Отказать» и
«Закрыть тикет»:

1. **Захват.** ``begin_transition`` условным UPDATE переводит `open` в
   `processing`. Захват удаётся ровно одному вызову, поэтому double-click
   и гонка accept/deny/close безопасны: проигравший получает
   «Этот тикет уже обработан».
2. **Acknowledgement.** Пользователю отвечаем сразу: дальше канал исчезнет.
3. **Транскрипт.** Снимок переписки собирается до удаления канала, его
   полнота показывается модератору.
4. **Audit-log.** Запись в лог-центр вместе с транскриптом; ID сообщения
   сохраняется для последующего удаления данных.
5. **Финализация состояния.** ``finalize_transition`` фиксирует конечный
   статус и ровно один раз пополняет дневную статистику.
6. **Удаление канала.** Последним шагом, когда данные уже сохранены.

Если шаг 3–4 падает с временной ошибкой Discord, заявка возвращается в
`open` (``release_transition``) — задача не теряется и повторяется
вручную или reconciliation-задачей. Противоречивое состояние «закрыто
в БД + канал жив» не возникает.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

import config
from database.tickets_db import (
    async_add_log_message_id,
    async_begin_transition,
    async_finalize_transition,
    async_get_ticket,
    async_release_transition,
)
from utils.errors import is_retryable, log_event, new_correlation_id
from utils.logcenter import LOG_KEY_DECISIONS, send_to_log
from utils.logger import logger

from .transcript import build_transcript


@dataclass
class TerminalOutcome:
    """Итог терминального действия для вызывающего UI."""

    ok: bool
    reason: str | None = None
    transcript_note: str | None = None
    correlation_id: str | None = None


async def claim_ticket(channel_id: int, status: str, guild_id: int | None) -> bool:
    """Захватывает заявку под терминальное действие текущего сервера."""
    return await async_begin_transition(channel_id, status, guild_id=guild_id)


async def release_ticket_claim(channel_id: int) -> bool:
    """Возвращает захваченную заявку в ``open`` после сбоя обработчика."""
    return await async_release_transition(channel_id)


async def complete_terminal_action(
    *,
    guild,
    channel,
    status: str,
    actor,
    reason: str | None,
    embed: discord.Embed,
    after_finalize: Callable[[], Awaitable[None]] | None = None,
    delete_channel: bool = True,
) -> TerminalOutcome:
    """Выполняет шаги 3–6 для уже захваченной заявки.

    ``after_finalize`` предназначен для внешних уведомлений. Он вызывается
    только после успешной фиксации статуса и до удаления канала: откат
    подготовки не оставит у заявителя ложного DM или объявления.
    """
    channel_id = getattr(channel, "id", None)
    correlation_id = new_correlation_id()
    actor_id = getattr(actor, "id", None)
    guild_id = getattr(guild, "id", None)

    transcript = await build_transcript(channel)
    if transcript.failed and not _may_proceed_without_transcript(transcript):
        await async_release_transition(channel_id)
        logger.error(
            f"ticket.terminal outcome=transcript_failed status={status} "
            f"channel_id={channel_id} correlation_id={correlation_id}"
        )
        return TerminalOutcome(
            ok=False,
            reason=(
                "Не удалось сохранить переписку тикета, поэтому действие отменено — "
                "канал остался на месте. Проверьте права бота на чтение истории "
                f"и повторите. Код: `{correlation_id}`"
            ),
            correlation_id=correlation_id,
        )

    embed.add_field(name="Архив переписки", value=transcript.status_note(), inline=False)

    permanent_log_error = False
    try:
        log_message = await send_to_log(
            guild,
            LOG_KEY_DECISIONS,
            embed=embed,
            files=transcript.files or None,
            raise_http_errors=True,
        )
    except discord.HTTPException as error:
        if is_retryable(error):
            await async_release_transition(channel_id)
            logger.warning(
                f"ticket.terminal outcome=log_retryable status={status} "
                f"channel_id={channel_id} error_type={type(error).__name__} "
                f"correlation_id={correlation_id}"
            )
            return TerminalOutcome(
                ok=False,
                reason=(
                    "Discord временно недоступен, заявка осталась открытой — "
                    f"повторите действие через минуту. Код: `{correlation_id}`"
                ),
                correlation_id=correlation_id,
            )
        permanent_log_error = True
        log_message = None
        logger.exception(
            f"ticket.terminal outcome=log_failed status={status} channel_id={channel_id} "
            f"correlation_id={correlation_id}"
        )

    if log_message is None and not permanent_log_error:
        await async_release_transition(channel_id)
        logger.warning(
            f"ticket.terminal outcome=log_unavailable status={status} "
            f"channel_id={channel_id} correlation_id={correlation_id}"
        )
        return TerminalOutcome(
            ok=False,
            reason=(
                "Не удалось сохранить аудит решения, заявка осталась открытой — "
                f"проверьте лог-центр и повторите действие. Код: `{correlation_id}`"
            ),
            correlation_id=correlation_id,
        )

    if log_message is not None:
        await async_add_log_message_id(channel_id, log_message.channel.id, log_message.id)

    finalized = await async_finalize_transition(
        channel_id, status, closed_by=actor_id, reason=reason
    )
    if not finalized:
        # состояние успели изменить конкурентно (например erasure) —
        # повторную финализацию не делаем, канал не трогаем
        logger.warning(
            f"ticket.terminal outcome=already_finalized status={status} "
            f"channel_id={channel_id} correlation_id={correlation_id}"
        )
        return TerminalOutcome(
            ok=False,
            reason=config.TICKET_ALREADY_DECIDED,
            correlation_id=correlation_id,
        )

    if after_finalize is not None:
        try:
            await after_finalize()
        except Exception as error:  # noqa: BLE001 - финализация уже состоялась
            logger.exception(
                f"ticket.terminal outcome=notify_failed status={status} channel_id={channel_id} "
                f"error_type={type(error).__name__} correlation_id={correlation_id}"
            )

    if delete_channel:
        await _delete_channel(channel, status, actor, correlation_id)

    log_event(
        "ticket.terminal",
        status=status,
        guild_id=guild_id,
        channel_id=channel_id,
        actor_id=actor_id,
        transcript_complete=transcript.complete,
        messages=transcript.message_count,
    )
    return TerminalOutcome(
        ok=True,
        transcript_note=transcript.status_note(),
        correlation_id=correlation_id,
    )


def _may_proceed_without_transcript(transcript) -> bool:
    """Пустой канал — не повод блокировать закрытие."""
    return transcript.message_count == 0 and transcript.error is None


async def _delete_channel(channel, status: str, actor, correlation_id: str) -> None:
    try:
        await channel.delete(reason=f"Тикет {status}: {actor}")
    except discord.NotFound:
        pass  # канал уже удалён — состояние согласовано
    except (discord.Forbidden, discord.HTTPException) as error:
        # запись уже финализирована и транскрипт сохранён; канал подберёт
        # reconciliation, но факт обязан быть виден оператору
        logger.exception(
            f"ticket.terminal outcome=channel_delete_failed status={status} "
            f"channel_id={getattr(channel, 'id', None)} error_type={type(error).__name__} "
            f"correlation_id={correlation_id}"
        )


async def ticket_for_channel(channel_id: int, guild_id: int | None):
    """Тикет канала, если он принадлежит этому серверу."""
    ticket = await async_get_ticket(channel_id)
    if ticket is None:
        return None
    if guild_id is not None and ticket["guild_id"] != guild_id:
        return None
    return ticket
