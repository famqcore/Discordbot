import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from afk.views import AfkMenuView, AfkReturnView, AfkSetModal, build_afk_embed, parse_return_time
from utils import clock


class TestParseReturnTime(unittest.TestCase):
    def test_time_format(self):
        result = parse_return_time("23:45")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, __import__("datetime").datetime)

    def test_hours_format(self):
        result = parse_return_time("2 часа")
        self.assertIsNotNone(result)

    def test_hours_short_format(self):
        result = parse_return_time("2 ч")
        self.assertIsNotNone(result)

    def test_minutes_format(self):
        result = parse_return_time("30 мин")
        self.assertIsNotNone(result)

    def test_minutes_short_format(self):
        result = parse_return_time("30 м")
        self.assertIsNotNone(result)

    def test_invalid_format(self):
        result = parse_return_time("abc")
        self.assertIsNone(result)

    def test_empty_string(self):
        result = parse_return_time("")
        self.assertIsNone(result)

    def test_no_number(self):
        result = parse_return_time("часов")
        self.assertIsNone(result)

    def test_case_insensitive(self):
        result1 = parse_return_time("2 ЧАСА")
        result2 = parse_return_time("2 часа")
        self.assertIsNotNone(result1)
        self.assertIsNotNone(result2)

    def test_past_time_returns_tomorrow(self):
        now = clock.utcnow()
        local_now = clock.to_local(now)
        past_time = f"{(local_now.hour - 1) % 24:02d}:00"

        result = parse_return_time(past_time, now)

        self.assertIsNotNone(result)
        self.assertGreater(result, now)

    def test_future_time_returns_today(self):
        now = clock.utcnow()
        local_now = clock.to_local(now)
        future_time = f"{(local_now.hour + 1) % 24:02d}:00"

        result = parse_return_time(future_time, now)

        self.assertIsNotNone(result)
        self.assertGreater(result, now)

    def test_invalid_minutes_rejected(self):
        self.assertIsNone(parse_return_time("23:99"))

    def test_suffix_not_matched_inside_words(self):
        # «вечера» не должно читаться как «4 часа» только из-за буквы «ч»
        self.assertIsNone(parse_return_time("в 4 вечера"))

    def test_number_attached_to_unit(self):
        self.assertIsNotNone(parse_return_time("2часа"))

    def test_plain_number_without_unit(self):
        self.assertIsNone(parse_return_time("30"))


class TestBuildAfkEmbed(unittest.TestCase):
    def test_empty_afk_list(self):
        guild = MagicMock()
        guild.id = 123

        with patch("afk.views.get_all_afk") as mock_get:
            mock_get.return_value = []
            embed = build_afk_embed(guild)

        self.assertIsInstance(embed, discord.Embed)
        self.assertEqual(embed.fields[0].value, "0 человек")

    def test_with_afk_users(self):
        guild = MagicMock()
        guild.id = 123
        guild.get_member = MagicMock(return_value=None)

        mock_rows = [
            {
                "user_id": 111,
                "afk_reason": "test",
                "afk_since": "2024-01-01T10:00:00.000000+00:00",
                "estimated_return": None,
            },
        ]

        with patch("afk.views.get_all_afk") as mock_get:
            mock_get.return_value = mock_rows
            embed = build_afk_embed(guild)

        self.assertIsInstance(embed, discord.Embed)
        self.assertEqual(embed.fields[0].value, "1 человек")
        self.assertIsNotNone(embed.description)

    def test_with_estimated_return(self):
        guild = MagicMock()
        guild.id = 123
        guild.get_member = MagicMock(return_value=None)

        mock_rows = [
            {
                "user_id": 111,
                "afk_reason": "test",
                "afk_since": "2024-01-01T10:00:00.000000+00:00",
                "estimated_return": "2024-01-01T12:00:00.000000+00:00",
            },
        ]

        with patch("afk.views.get_all_afk") as mock_get:
            mock_get.return_value = mock_rows
            embed = build_afk_embed(guild)

        self.assertIn("12:00", embed.description)

    def test_with_member_object(self):
        guild = MagicMock()
        guild.id = 123
        member = MagicMock()
        member.mention = "<@111>"
        guild.get_member = MagicMock(return_value=member)

        mock_rows = [
            {
                "user_id": 111,
                "afk_reason": "test",
                "afk_since": "2024-01-01T10:00:00.000000+00:00",
                "estimated_return": None,
            },
        ]

        with patch("afk.views.get_all_afk") as mock_get:
            mock_get.return_value = mock_rows
            embed = build_afk_embed(guild)

        self.assertIn("<@111>", embed.description)


class TestAfkMenuView(unittest.TestCase):
    def test_has_three_buttons(self):
        view = AfkMenuView()
        self.assertEqual(len(view.children), 3)

    def test_leave_button(self):
        view = AfkMenuView()
        btn = view.children[0]
        self.assertEqual(btn.label, config.AFK_BUTTON_LEAVE)
        self.assertEqual(btn.style, discord.ButtonStyle.danger)

    def test_return_button(self):
        view = AfkMenuView()
        btn = view.children[1]
        self.assertEqual(btn.label, config.AFK_BUTTON_RETURN)
        self.assertEqual(btn.style, discord.ButtonStyle.success)

    def test_refresh_button(self):
        view = AfkMenuView()
        btn = view.children[2]
        self.assertEqual(btn.label, config.AFK_BUTTON_REFRESH)
        self.assertEqual(btn.style, discord.ButtonStyle.primary)

    def test_timeout_none(self):
        view = AfkMenuView()
        self.assertIsNone(view.timeout)


class TestAfkMenuViewButtons(unittest.IsolatedAsyncioTestCase):
    async def test_leave_button_sends_modal(self):
        view = AfkMenuView()
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.guild_id = 123
        interaction.guild = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        await view.leave.callback(interaction)
        interaction.response.send_modal.assert_called_once()

    async def test_return_button_not_afk(self):
        view = AfkMenuView()
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 456
        interaction.guild_id = 123
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.views.get_afk_user") as mock_get:
            mock_get.return_value = None
            await view.return_btn.callback(interaction)

        interaction.response.send_message.assert_called_once()

    async def test_return_button_is_afk(self):
        view = AfkMenuView()
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 456
        interaction.guild_id = 123
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        mock_row = {"afk_since": clock.to_db(clock.utcnow())}

        with patch("afk.views.get_afk_user") as mock_get:
            mock_get.return_value = mock_row
            await view.return_btn.callback(interaction)

        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args
        self.assertIn("view", call_args.kwargs)

    async def test_refresh_button(self):
        view = AfkMenuView()
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.id = 123
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.views.get_all_afk") as mock_get:
            mock_get.return_value = []
            await view.refresh.callback(interaction)

        interaction.response.send_message.assert_called_once()
        self.assertTrue(interaction.response.send_message.call_args.kwargs.get("ephemeral"))


class TestAfkReturnView(unittest.TestCase):
    def test_init(self):
        member = MagicMock()
        view = AfkReturnView(member, 123, "1 час")
        self.assertEqual(view.guild_id, 123)
        self.assertEqual(view.duration_text, "1 час")

    def test_has_two_buttons(self):
        member = MagicMock()
        view = AfkReturnView(member, 123, "1 час")
        self.assertEqual(len(view.children), 2)

    def test_confirm_button(self):
        member = MagicMock()
        view = AfkReturnView(member, 123, "1 час")
        btn = view.children[0]
        self.assertEqual(btn.label, config.AFK_BUTTON_RETURN)
        self.assertEqual(btn.style, discord.ButtonStyle.success)

    def test_cancel_button(self):
        member = MagicMock()
        view = AfkReturnView(member, 123, "1 час")
        btn = view.children[1]
        self.assertEqual(btn.label, config.AFK_BUTTON_STAY)
        self.assertEqual(btn.style, discord.ButtonStyle.danger)


class TestAfkReturnViewButtons(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_wrong_user(self):
        member = MagicMock()
        member.id = 456
        view = AfkReturnView(member, 123, "1 час")
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 999
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await view.confirm.callback(interaction)
        interaction.response.send_message.assert_called_once()

    async def test_confirm_success(self):
        member = MagicMock()
        member.id = 456
        member.edit = AsyncMock()
        view = AfkReturnView(member, 123, "1 час")
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 456
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        session = {"duration_seconds": 3600, "original_nick": "Вася", "nick_applied": 1}
        with patch("afk.views.take_afk_session", return_value=session):
            with patch("afk.views.remove_afk_nickname", new_callable=AsyncMock) as mock_nick:
                with patch("afk.views.send_to_log", new_callable=AsyncMock):
                    await view.confirm.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        mock_nick.assert_awaited_once_with(member, "Вася", nick_applied=True)

    async def test_confirm_not_afk(self):
        member = MagicMock()
        member.id = 456
        view = AfkReturnView(member, 123, "1 час")
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 456
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.views.take_afk_session", return_value=None):
            await view.confirm.callback(interaction)

        interaction.response.send_message.assert_awaited_once()

    async def test_cancel_wrong_user(self):
        member = MagicMock()
        member.id = 456
        view = AfkReturnView(member, 123, "1 час")
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 999
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await view.cancel.callback(interaction)
        interaction.response.send_message.assert_called_once()

    async def test_cancel_success(self):
        member = MagicMock()
        member.id = 456
        view = AfkReturnView(member, 123, "1 час")
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 456
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        await view.cancel.callback(interaction)
        interaction.response.edit_message.assert_called_once()


class TestAfkSetModal(unittest.TestCase):
    def test_modal_title(self):
        member = MagicMock()
        modal = AfkSetModal(member, 123, MagicMock())
        self.assertEqual(modal.title, config.AFK_MODAL_TITLE)

    def test_has_two_inputs(self):
        member = MagicMock()
        modal = AfkSetModal(member, 123, MagicMock())
        self.assertEqual(len(modal.children), 2)

    def test_reason_input(self):
        member = MagicMock()
        modal = AfkSetModal(member, 123, MagicMock())
        inp = modal.children[0]
        self.assertEqual(inp.label, config.AFK_MODAL_REASON_LABEL)
        self.assertFalse(inp.required)

    def test_duration_input(self):
        member = MagicMock()
        modal = AfkSetModal(member, 123, MagicMock())
        inp = modal.children[1]
        self.assertEqual(inp.label, config.AFK_MODAL_DURATION_LABEL)
        self.assertTrue(inp.required)


class TestAfkSetModalSubmit(unittest.IsolatedAsyncioTestCase):
    async def test_submit_invalid_time(self):
        member = MagicMock()
        member.id = 456
        modal = AfkSetModal(member, 123, MagicMock())
        modal.reason = MagicMock()
        modal.reason.value = "test"
        modal.duration = MagicMock()
        modal.duration.value = "invalid"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called_once()
        self.assertIn("формат времени", interaction.response.send_message.await_args.args[0])

    async def test_submit_valid_time(self):
        member = MagicMock()
        member.id = 456
        member.edit = AsyncMock()
        modal = AfkSetModal(member, 123, MagicMock())
        modal.reason = MagicMock()
        modal.reason.value = "test reason"
        modal.duration = MagicMock()
        modal.duration.value = "1 час"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.views.get_afk_user", return_value=None):
            with patch(
                "afk.views.set_afk", return_value={"created": True, "updated": False}
            ) as mock_set:
                with patch("afk.views.mark_nick_applied"):
                    with patch(
                        "afk.views.add_afk_nickname", new_callable=AsyncMock, return_value=True
                    ):
                        with patch("afk.views.send_to_log", new_callable=AsyncMock):
                            await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        mock_set.assert_called_once()

    async def test_submit_default_reason(self):
        member = MagicMock()
        member.id = 456
        member.edit = AsyncMock()
        modal = AfkSetModal(member, 123, MagicMock())
        modal.reason = MagicMock()
        modal.reason.value = ""
        modal.duration = MagicMock()
        modal.duration.value = "1 час"

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        with patch("afk.views.get_afk_user", return_value=None):
            with patch(
                "afk.views.set_afk", return_value={"created": True, "updated": False}
            ) as mock_set:
                with patch("afk.views.mark_nick_applied"):
                    with patch(
                        "afk.views.add_afk_nickname", new_callable=AsyncMock, return_value=True
                    ):
                        with patch("afk.views.send_to_log", new_callable=AsyncMock):
                            await modal.on_submit(interaction)

        self.assertEqual(mock_set.call_args.args[2], config.AFK_REASON_DEFAULT)


if __name__ == "__main__":
    unittest.main()
