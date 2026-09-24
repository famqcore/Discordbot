"""Единый error boundary.

Проверяется: traceback с correlation id, ровно один безопасный ответ
пользователю, разделение ожидаемых ошибок Discord и настоящих сбоев,
отсутствие PII в логах, политика повторов и поведение фоновых задач.
"""

import asyncio
import sqlite3
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tests.support import FakeInteraction, http_exception, make_forbidden, make_not_found
from utils.errors import (
    InteractionErrorBoundary,
    format_context,
    guard_background,
    is_retryable,
    log_event,
    new_correlation_id,
    respond_error,
    retry_discord,
)


class CorrelationIdTestCase(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = {new_correlation_id() for _ in range(100)}

        self.assertEqual(len(ids), 100)

    def test_id_is_short_and_printable(self):
        value = new_correlation_id()

        self.assertLessEqual(len(value), 12)
        self.assertTrue(value.isalnum())


class FormatContextTestCase(unittest.TestCase):
    def test_key_value_pairs(self):
        self.assertEqual(format_context(guild_id=1, user_id=2), "guild_id=1 user_id=2")

    def test_none_values_skipped(self):
        self.assertEqual(format_context(guild_id=1, channel_id=None), "guild_id=1")

    def test_empty_context(self):
        self.assertEqual(format_context(), "")


class RetryPolicyTestCase(unittest.TestCase):
    def test_server_errors_are_retryable(self):
        for status in (500, 502, 503, 504):
            self.assertTrue(is_retryable(http_exception(status)))

    def test_client_errors_are_not_retryable(self):
        for status in (400, 403, 404):
            self.assertFalse(is_retryable(http_exception(status)))

    def test_sqlite_busy_is_retryable(self):
        self.assertTrue(is_retryable(sqlite3.OperationalError("database is locked")))

    def test_other_sqlite_errors_are_not_retryable(self):
        self.assertFalse(is_retryable(sqlite3.OperationalError("no such table: x")))


class RetryDiscordTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_returns_result_without_retry(self):
        operation = AsyncMock(return_value="ok")

        result = await retry_discord(operation, event="test")

        self.assertEqual(result, "ok")
        self.assertEqual(operation.await_count, 1)

    async def test_retries_transient_error_then_succeeds(self):
        operation = AsyncMock(side_effect=[http_exception(503), "ok"])

        with (
            patch("utils.errors.asyncio.sleep", new_callable=AsyncMock),
            patch("utils.errors.logger"),
        ):
            result = await retry_discord(operation, event="test")

        self.assertEqual(result, "ok")
        self.assertEqual(operation.await_count, 2)

    async def test_permanent_error_is_not_retried(self):
        operation = AsyncMock(side_effect=make_forbidden())

        with self.assertRaises(discord.Forbidden):
            await retry_discord(operation, event="test")

        self.assertEqual(operation.await_count, 1)

    async def test_gives_up_after_max_attempts(self):
        operation = AsyncMock(side_effect=http_exception(503))

        with (
            patch("utils.errors.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("utils.errors.logger"),
        ):
            with self.assertRaises(discord.HTTPException):
                await retry_discord(operation, event="test", attempts=3)

        self.assertEqual(operation.await_count, 3)
        self.assertEqual(mock_sleep.await_count, 2)

    async def test_backoff_grows(self):
        operation = AsyncMock(side_effect=http_exception(503))

        with (
            patch("utils.errors.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("utils.errors.logger"),
        ):
            with self.assertRaises(discord.HTTPException):
                await retry_discord(operation, event="test", attempts=3)

        delays = [call.args[0] for call in mock_sleep.await_args_list]
        self.assertLess(delays[0], delays[1])


class RespondErrorTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_first_response_used_when_available(self):
        interaction = FakeInteraction()

        await respond_error(interaction, "ошибка")

        interaction.response.send_message.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()

    async def test_followup_used_when_already_answered(self):
        interaction = FakeInteraction()
        await interaction.response.send_message("первый ответ")

        await respond_error(interaction, "ошибка")

        interaction.followup.send.assert_awaited_once()

    async def test_already_acknowledged_interaction_falls_back_to_followup(self):
        interaction = FakeInteraction()
        interaction.response.send_message = AsyncMock(
            side_effect=discord.InteractionResponded(MagicMock())
        )

        await respond_error(interaction, "ошибка")

        interaction.followup.send.assert_awaited_once()

    async def test_expired_interaction_does_not_raise(self):
        interaction = FakeInteraction()
        interaction.response.send_message = AsyncMock(side_effect=http_exception(404))

        with patch("utils.errors.logger"):
            await respond_error(interaction, "ошибка")  # не должно бросить

    async def test_correlation_id_included(self):
        interaction = FakeInteraction()

        await respond_error(interaction, "ошибка", correlation_id="abc123")

        self.assertIn("abc123", interaction.response.send_message.await_args.args[0])


class InteractionBoundaryTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_is_transparent(self):
        interaction = FakeInteraction()

        async with InteractionErrorBoundary(interaction, "test.event"):
            pass

        interaction.response.send_message.assert_not_awaited()

    async def test_unexpected_error_logged_with_traceback(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise RuntimeError("boom")

        mock_logger.exception.assert_called_once()
        interaction.response.send_message.assert_awaited_once()

    async def test_user_message_contains_correlation_id_from_log(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise RuntimeError("boom")

        text = interaction.response.send_message.await_args.args[0]
        correlation_id = text.split("`")[1]
        self.assertIn(correlation_id, mock_logger.exception.call_args.args[0])

    async def test_error_text_hides_internal_details(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger"):
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise RuntimeError("секрет базы данных")

        text = interaction.response.send_message.await_args.args[0]
        self.assertNotIn("секрет", text)
        self.assertNotIn("RuntimeError", text)

    async def test_forbidden_logged_as_warning_not_exception(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise make_forbidden()

        mock_logger.warning.assert_called_once()
        mock_logger.exception.assert_not_called()

    async def test_not_found_logged_as_warning(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise make_not_found()

        mock_logger.warning.assert_called_once()

    async def test_server_error_logged_as_exception(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise http_exception(503)

        mock_logger.exception.assert_called_once()

    async def test_sqlite_error_logged_as_exception(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise sqlite3.OperationalError("database is locked")

        mock_logger.exception.assert_called_once()
        interaction.response.send_message.assert_awaited_once()

    async def test_cancellation_is_propagated(self):
        interaction = FakeInteraction()

        with self.assertRaises(asyncio.CancelledError):
            async with InteractionErrorBoundary(interaction, "test.event"):
                raise asyncio.CancelledError()

    async def test_user_answered_only_once_after_partial_work(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger"):
            async with InteractionErrorBoundary(interaction, "test.event"):
                await interaction.response.send_message("работаю…")
                raise RuntimeError("boom")

        self.assertEqual(interaction.response.send_message.await_count, 1)
        self.assertEqual(interaction.followup.send.await_count, 1)

    async def test_logged_context_has_no_payload(self):
        interaction = FakeInteraction()

        with patch("utils.errors.logger") as mock_logger:
            async with InteractionErrorBoundary(interaction, "test.event", ticket_id=5):
                raise RuntimeError("boom")

        logged = mock_logger.exception.call_args.args[0]
        self.assertIn("ticket_id=5", logged)
        self.assertIn("guild_id=", logged)
        self.assertNotIn("boom", logged)


class GuardBackgroundTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_returns_value_of_async_operation(self):
        result = await guard_background(AsyncMock(return_value=42), event="test")

        self.assertEqual(result, 42)

    async def test_supports_sync_operation(self):
        """Обращения к БД синхронны — оборачивать их в корутину не нужно."""
        result = await guard_background(lambda: 7, event="test")

        self.assertEqual(result, 7)

    async def test_error_is_swallowed_and_logged(self):
        def boom():
            raise RuntimeError("db down")

        with patch("utils.errors.logger") as mock_logger:
            result = await guard_background(boom, event="test")

        self.assertIsNone(result)
        mock_logger.exception.assert_called_once()

    async def test_cancellation_is_propagated(self):
        async def cancelled():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await guard_background(cancelled, event="test")

    async def test_context_fields_are_logged(self):
        def boom():
            raise RuntimeError("x")

        with patch("utils.errors.logger") as mock_logger:
            await guard_background(boom, event="test", guild_id=1)

        self.assertIn("guild_id=1", mock_logger.exception.call_args.args[0])


class LogEventTestCase(unittest.TestCase):
    def test_structured_line(self):
        with patch("utils.errors.logger") as mock_logger:
            log_event("ticket.create", guild_id=1, channel_id=2)

        message = mock_logger.log.call_args.args[1]
        self.assertIn("ticket.create", message)
        self.assertIn("outcome=ok", message)
        self.assertIn("guild_id=1", message)


if __name__ == "__main__":
    unittest.main()
