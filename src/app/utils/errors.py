"""Единый error boundary и структурированные логи (issue #20).

Правила:

- у каждой неуспешной критической операции есть diagnosable-след:
  ``logger.exception`` с traceback, correlation id и структурированным
  контекстом (event, guild/channel/ticket ID, outcome);
- в лог не попадают содержимое формы, причины, токены и прочие PII —
  только идентификаторы и исход;
- пользователь получает безопасный текст ровно один раз: ``respond_error``
  сам выбирает между первым ответом, followup и правкой отложенного;
- ожидаемые ошибки Discord (``Forbidden``, ``NotFound``) отделены от сбоев
  API (``HTTPException`` 5xx) и от ошибок БД (``sqlite3.Error``);
- временные ошибки (5xx, busy) повторяются с ограниченным backoff через
  ``retry_discord``, ошибки уборки не подавляются молча.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import discord

from utils.logger import logger

T = TypeVar("T")

USER_ERROR_TEXT = "⚠️ Не удалось выполнить действие. Попробуйте ещё раз позже."
USER_ERROR_WITH_ID = "⚠️ Не удалось выполнить действие. Код ошибки для администратора: `{id}`"

RETRYABLE_STATUS = (500, 502, 503, 504)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5


def new_correlation_id() -> str:
    """Короткий идентификатор для связи сообщения пользователю и записи в логе."""
    return uuid.uuid4().hex[:8]


def format_context(**fields: Any) -> str:
    """`key=value` пары в стабильном порядке; None-поля опускаются."""
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    return " ".join(parts)


def log_event(event: str, outcome: str = "ok", level: int = 20, **fields: Any) -> None:
    """Структурированная запись о завершённой операции."""
    logger.log(level, f"{event} outcome={outcome} {format_context(**fields)}".strip())


def log_failure(event: str, error: BaseException, correlation_id: str, **fields: Any) -> None:
    """Запись о сбое с traceback и correlation id."""
    logger.exception(
        f"{event} outcome=error error_type={type(error).__name__} "
        f"correlation_id={correlation_id} {format_context(**fields)}".strip()
    )


def is_retryable(error: BaseException) -> bool:
    """Временная ли это ошибка: 5xx/таймаут Discord или занятая БД."""
    if isinstance(error, discord.HTTPException):
        return getattr(error, "status", None) in RETRYABLE_STATUS
    if isinstance(error, sqlite3.OperationalError):
        text = str(error).lower()
        return "locked" in text or "busy" in text
    return isinstance(error, TimeoutError | asyncio.TimeoutError | discord.DiscordServerError)


async def retry_discord(
    operation: Callable[[], Awaitable[T]],
    *,
    event: str,
    attempts: int = MAX_RETRIES,
    **fields: Any,
) -> T:
    """Повтор временных ошибок с экспоненциальным backoff.

    Постоянные ошибки (Forbidden, NotFound, 4xx) пробрасываются сразу:
    повторять их бессмысленно и вредно для rate limit.
    """
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except (discord.HTTPException, sqlite3.Error, TimeoutError) as error:
            last_error = error
            if not is_retryable(error) or attempt == attempts:
                raise
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"{event} outcome=retry attempt={attempt}/{attempts} "
                f"error_type={type(error).__name__} delay={delay:.1f}s "
                f"{format_context(**fields)}".strip()
            )
            await asyncio.sleep(delay)
    raise last_error  # pragma: no cover - недостижимо: цикл всегда либо вернёт, либо бросит


async def respond_error(
    interaction: discord.Interaction,
    text: str,
    *,
    correlation_id: str | None = None,
) -> None:
    """Отвечает пользователю ровно один раз, чем бы ни закончился interaction."""
    message = text if correlation_id is None else f"{text}\n`{correlation_id}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.InteractionResponded:
        try:
            await interaction.followup.send(message, ephemeral=True)
        except discord.HTTPException as error:
            logger.warning(f"error_boundary outcome=reply_failed error_type={type(error).__name__}")
    except discord.HTTPException as error:
        # interaction протух (15 минут) или Discord недоступен — терять нечего,
        # но факт неотвеченного пользователя должен быть виден
        logger.warning(f"error_boundary outcome=reply_failed error_type={type(error).__name__}")


def interaction_context(interaction: discord.Interaction) -> dict[str, Any]:
    """Безопасный контекст interaction: только идентификаторы."""
    return {
        "guild_id": getattr(interaction, "guild_id", None),
        "channel_id": getattr(getattr(interaction, "channel", None), "id", None),
        "user_id": getattr(getattr(interaction, "user", None), "id", None),
    }


class InteractionErrorBoundary:
    """Контекстный менеджер для callback-ов Discord UI.

    Логирует traceback с correlation id и отвечает пользователю безопасным
    текстом. Ожидаемые ``Forbidden``/``NotFound`` тоже фиксируются, но как
    предупреждение: это конфигурация сервера, а не сбой бота.
    """

    def __init__(
        self,
        interaction: discord.Interaction,
        event: str,
        user_message: str = USER_ERROR_WITH_ID,
        **fields: Any,
    ) -> None:
        self.interaction = interaction
        self.event = event
        self.user_message = user_message
        self.fields = {**interaction_context(interaction), **fields}
        self.correlation_id = new_correlation_id()

    async def __aenter__(self) -> InteractionErrorBoundary:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if exc is None:
            return False
        if isinstance(exc, asyncio.CancelledError):
            return False

        if isinstance(exc, discord.Forbidden | discord.NotFound):
            logger.warning(
                f"{self.event} outcome=discord_denied error_type={type(exc).__name__} "
                f"correlation_id={self.correlation_id} {format_context(**self.fields)}".strip()
            )
        else:
            log_failure(self.event, exc, self.correlation_id, **self.fields)

        text = self.user_message.format(id=self.correlation_id)
        await respond_error(self.interaction, text)
        return True


async def guard_background(
    operation: Callable[[], Awaitable[T] | T],
    *,
    event: str,
    **fields: Any,
) -> T | None:
    """Error boundary для фоновых задач: сбой не должен ронять цикл.

    Принимает и корутинные, и синхронные операции: обращения к БД идут
    через шлюз и возвращают готовое значение, оборачивать их в корутину
    только ради boundary не нужно.
    """
    try:
        result = operation()
        if inspect.isawaitable(result):
            return await result
        return result
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - верхний уровень фоновой задачи
        log_failure(event, error, new_correlation_id(), **fields)
        return None
