"""Issue #6: реальное удаление персональных данных (!delete_user_data).

Интеграционные сценарии: открытый тикет, закрытый тикет, повторное
удаление (идемпотентность), изоляция по серверу и подтверждение команды.
"""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from database import afk_db, migrate_schema, state_db, tickets_db
from tickets.commands import DeleteUserDataConfirmView, TicketsCog
from tickets.erasure import erase_user_data

PII_ANSWERS = '{"OOC имя и возраст(IRL)": "Иван, 21"}'


class ErasureDBTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self._old_db_path = config.DB_PATH
        config.DB_PATH = self.temp.name
        migrate_schema()

    def tearDown(self):
        config.DB_PATH = self._old_db_path
        try:
            os.unlink(self.temp.name)
        except OSError:
            pass


class TestEraseUserData(ErasureDBTestCase, unittest.IsolatedAsyncioTestCase):
    def _make_guild_and_channel(self, channel_id):
        channel = MagicMock()
        channel.id = channel_id
        channel.delete = AsyncMock()
        guild = MagicMock()
        guild.id = 7
        guild.get_channel = MagicMock(
            side_effect=lambda cid: channel if cid == channel_id else None
        )
        return guild, channel

    def _make_member(self, user_id=42):
        member = MagicMock()
        member.id = user_id
        member.nick = "[AFK] Вася"
        return member

    async def test_open_ticket_channel_and_logs_deleted_and_row_anonymized(self):
        tickets_db.save_ticket(
            100, 42, "vasya", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-01T10:00:00", guild_id=7
        )
        tickets_db.add_log_message_id(100, 900, 500)
        afk_db.set_afk(42, 7, "дела", "2024-01-01T11:00:00", original_nick="Вася")

        guild, channel = self._make_guild_and_channel(100)
        member = self._make_member()

        with patch("tickets.erasure.delete_log_messages", new_callable=AsyncMock) as mock_logs:
            with patch("tickets.erasure.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                mock_logs.return_value = 1
                counts = await erase_user_data(guild, member)

        # открытый тикет удалён вместе с каналом, транскрипты — тоже
        channel.delete.assert_awaited_once()
        mock_logs.assert_awaited_once()
        self.assertEqual(mock_logs.call_args.args[1], [(900, 500)])
        mock_nick.assert_awaited_once()
        self.assertEqual(mock_nick.call_args.args[1], "Вася")

        self.assertEqual(counts["tickets"], 1)
        self.assertEqual(counts["channels"], 1)
        self.assertEqual(counts["log_messages"], 1)
        self.assertEqual(counts["afk_users"], 1)

        ticket = tickets_db.get_ticket(100)
        self.assertEqual(ticket["user_id"], 0)
        self.assertEqual(ticket["user_name"], "deleted-user")
        self.assertEqual(ticket["answers"], "{}")
        self.assertEqual(ticket["status"], "closed")
        self.assertIsNotNone(ticket["closed_at"])
        self.assertIsNone(ticket["log_message_ids"])
        self.assertIsNone(afk_db.get_afk_user(42, 7))

    async def test_closed_ticket_logs_deleted_row_anonymized_no_channel_touch(self):
        tickets_db.save_ticket(
            200, 42, "vasya", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-01T10:00:00", guild_id=7
        )
        tickets_db.update_ticket_status(200, "accepted", 9, "ок")
        tickets_db.add_log_message_id(200, 900, 501)

        guild, channel = self._make_guild_and_channel(200)
        member = self._make_member()

        with patch("tickets.erasure.delete_log_messages", new_callable=AsyncMock) as mock_logs:
            with patch("tickets.erasure.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                mock_logs.return_value = 1
                counts = await erase_user_data(guild, member)

        # канала давно нет (закрытие удаляет канал) — трогать нечего
        channel.delete.assert_not_called()
        mock_logs.assert_awaited_once()
        self.assertEqual(mock_logs.call_args.args[1], [(900, 501)])
        mock_nick.assert_not_called()  # пользователь не в AFK

        self.assertEqual(counts["tickets"], 1)
        self.assertEqual(counts["channels"], 0)

        ticket = tickets_db.get_ticket(200)
        self.assertEqual(ticket["status"], "accepted")
        self.assertEqual(ticket["user_id"], 0)
        self.assertEqual(ticket["answers"], "{}")
        self.assertIsNone(ticket["reason"])

    async def test_repeated_erasure_is_noop(self):
        tickets_db.save_ticket(
            100, 42, "vasya", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-01T10:00:00", guild_id=7
        )
        tickets_db.add_log_message_id(100, 900, 500)
        guild, _ = self._make_guild_and_channel(100)
        member = self._make_member()

        with patch("tickets.erasure.delete_log_messages", new_callable=AsyncMock) as mock_logs:
            mock_logs.return_value = 1
            with patch("tickets.erasure.remove_afk_nickname", new_callable=AsyncMock):
                first = await erase_user_data(guild, member)
            second = await erase_user_data(guild, member)

        self.assertEqual(first["tickets"], 1)
        # повторный запуск: пользователь уже анонимизирован, счётчики нулевые
        self.assertEqual(second["tickets"], 0)
        self.assertEqual(second["afk_users"], 0)
        self.assertEqual(mock_logs.await_count, 1)

    async def test_other_guild_and_other_users_untouched(self):
        tickets_db.save_ticket(
            100, 42, "vasya", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-01T10:00:00", guild_id=7
        )
        tickets_db.save_ticket(
            101, 42, "vasya-alt", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-02T10:00:00", guild_id=8
        )
        tickets_db.save_ticket(
            102, 55, "petya", "RP ЗАЯВКА", "rp", PII_ANSWERS, "2024-01-03T10:00:00", guild_id=7
        )

        guild, _ = self._make_guild_and_channel(100)
        member = self._make_member()

        with patch("tickets.erasure.delete_log_messages", new_callable=AsyncMock):
            with patch("tickets.erasure.remove_afk_nickname", new_callable=AsyncMock):
                await erase_user_data(guild, member)

        other_guild_ticket = tickets_db.get_ticket(101)
        self.assertEqual(other_guild_ticket["user_id"], 42)
        other_user_ticket = tickets_db.get_ticket(102)
        self.assertEqual(other_user_ticket["user_id"], 55)
        self.assertEqual(other_user_ticket["answers"], PII_ANSWERS)


class TestDeleteUserDataConfirmation(unittest.IsolatedAsyncioTestCase):
    """Команда требует явного подтверждения администратора."""

    def _make_interaction(self, guild_id=7, user_id=99):
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.id = guild_id
        interaction.user = MagicMock()
        interaction.user.id = user_id
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        return interaction

    async def test_command_sends_confirmation_view_without_pings(self):
        cog = TicketsCog(MagicMock())
        ctx = MagicMock()
        ctx.author.id = 99
        ctx.guild.id = 7
        ctx.send = AsyncMock()
        member = MagicMock()
        member.id = 42
        member.mention = "<@42>"

        await cog.delete_user_data.callback(cog, ctx, member)

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.call_args.kwargs
        self.assertIsInstance(kwargs["view"], DeleteUserDataConfirmView)
        self.assertEqual(kwargs["allowed_mentions"].users, [])
        self.assertEqual(kwargs["allowed_mentions"].roles, [])

    async def test_confirm_erases_and_reports(self):
        member = MagicMock()
        member.id = 42
        view = DeleteUserDataConfirmView(99, member)
        interaction = self._make_interaction()

        counts = {
            "tickets": 2,
            "channels": 1,
            "log_messages": 3,
            "afk_users": 1,
            "afk_stats": 1,
            "afk_cooldown": 4,
        }
        with patch("tickets.commands.erase_user_data", new_callable=AsyncMock) as mock_erase:
            with patch("tickets.commands.audit_erasure", new_callable=AsyncMock) as mock_audit:
                mock_erase.return_value = counts
                await view.confirm.callback(interaction)

        mock_erase.assert_awaited_once()
        mock_audit.assert_awaited_once()
        text = interaction.edit_original_response.call_args.kwargs["content"]
        self.assertIn("2", text)
        self.assertIsNone(interaction.edit_original_response.call_args.kwargs["view"])

    async def test_confirm_erasure_failure_reported(self):
        member = MagicMock()
        member.id = 42
        view = DeleteUserDataConfirmView(99, member)
        interaction = self._make_interaction()

        with patch(
            "tickets.commands.erase_user_data",
            new_callable=AsyncMock,
            side_effect=Exception("база упала"),
        ):
            await view.confirm.callback(interaction)

        text = interaction.edit_original_response.call_args.kwargs["content"]
        self.assertIn("ошибкой", text)

    async def test_other_user_cannot_press_buttons(self):
        member = MagicMock()
        member.id = 42
        view = DeleteUserDataConfirmView(99, member)
        interaction = self._make_interaction(user_id=100500)  # не админ, вызвавший команду

        with patch("tickets.commands.erase_user_data", new_callable=AsyncMock) as mock_erase:
            await view.confirm.callback(interaction)
            await view.cancel.callback(interaction)

        mock_erase.assert_not_called()
        self.assertEqual(interaction.response.send_message.await_count, 2)

    async def test_cancel_leaves_data_untouched(self):
        member = MagicMock()
        member.id = 42
        view = DeleteUserDataConfirmView(99, member)
        interaction = self._make_interaction()

        with patch("tickets.commands.erase_user_data", new_callable=AsyncMock) as mock_erase:
            await view.cancel.callback(interaction)

        mock_erase.assert_not_called()
        text = interaction.response.edit_message.call_args.kwargs["content"]
        self.assertIn("отменено", text)


class TestStateDb(ErasureDBTestCase):
    def test_set_get_delete_roundtrip(self):
        self.assertIsNone(state_db.get_state("k"))
        state_db.set_state("k", "v1")
        self.assertEqual(state_db.get_state("k"), "v1")
        state_db.set_state("k", "v2")
        self.assertEqual(state_db.get_state("k"), "v2")
        state_db.delete_state("k")
        self.assertIsNone(state_db.get_state("k"))


if __name__ == "__main__":
    unittest.main()
