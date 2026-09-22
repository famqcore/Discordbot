import unittest
from unittest.mock import patch

import config


class TestConfig(unittest.TestCase):
    def test_token_exists(self):
        self.assertIsNotNone(config.TOKEN)
        self.assertIsInstance(config.TOKEN, str)

    def test_db_path_is_string(self):
        self.assertIsInstance(config.DB_PATH, str)
        self.assertTrue(len(config.DB_PATH) > 0)

    def test_log_dir_is_string(self):
        self.assertIsInstance(config.LOG_DIR, str)
        self.assertTrue(config.LOG_DIR.endswith("logs"))

    def test_category_name(self):
        self.assertIsInstance(config.TICKETS_CATEGORY_NAME, str)
        self.assertTrue(len(config.TICKETS_CATEGORY_NAME) > 0)

    def test_roles_are_strings(self):
        roles = [
            config.ROLE_APPLIED,
            config.ROLE_RECRUITER,
            config.ROLE_OWNER,
            config.ROLE_DEP_OWNER,
            config.ROLE_ADMIN,
            config.ROLE_SUPPORT,
        ]
        for role in roles:
            self.assertIsInstance(role, str)
            self.assertTrue(len(role) > 0)

    def test_channel_names(self):
        self.assertIsInstance(config.LOG_CHANNEL_NAME, str)
        self.assertIsInstance(config.VOICE_CHANNELS, list)
        self.assertEqual(len(config.VOICE_CHANNELS), 3)
        for ch in config.VOICE_CHANNELS:
            self.assertIsInstance(ch, str)

    def test_commands(self):
        self.assertEqual(config.CMD_PREFIX, "!")
        self.assertEqual(config.CMD_FAMQCORE, "famqcore")
        self.assertEqual(config.CMD_STATS, "stats")
        self.assertEqual(config.CMD_HISTORY, "history")

    def test_dm_message(self):
        self.assertIsInstance(config.DM_MESSAGE, str)
        self.assertIn("FAMQCORE", config.DM_MESSAGE)

    def test_ticket_titles(self):
        self.assertIsInstance(config.TICKET_RP_TITLE, str)
        self.assertIsInstance(config.TICKET_CAPT_TITLE, str)
        self.assertTrue(len(config.TICKET_RP_TITLE) > 0)
        self.assertTrue(len(config.TICKET_CAPT_TITLE) > 0)

    def test_famqcore_embed(self):
        self.assertIsInstance(config.FAMQCORE_EMBED_TITLE, str)
        self.assertIsInstance(config.FAMQCORE_EMBED_DESCRIPTION, str)
        self.assertIn("FAMQCORE", config.FAMQCORE_EMBED_TITLE)

    def test_rp_fields_is_list(self):
        self.assertIsInstance(config.RP_FIELDS, list)
        self.assertEqual(len(config.RP_FIELDS), 5)
        for field in config.RP_FIELDS:
            self.assertEqual(len(field), 4)
            label, placeholder, required, max_length = field
            self.assertIsInstance(label, str)
            self.assertIsInstance(placeholder, str)
            self.assertIsInstance(required, bool)
            self.assertIsInstance(max_length, int)

    def test_capt_fields_is_list(self):
        self.assertIsInstance(config.CAPT_FIELDS, list)
        self.assertEqual(len(config.CAPT_FIELDS), 5)

    def test_error_messages(self):
        self.assertIsInstance(config.ERROR_TICKET_CREATE, str)

    def test_afk_embed_title(self):
        self.assertIsInstance(config.AFK_EMBED_TITLE, str)
        self.assertIn("AFK", config.AFK_EMBED_TITLE)

    def test_afk_embed_description(self):
        self.assertIsInstance(config.AFK_EMBED_DESCRIPTION, str)
        self.assertTrue(len(config.AFK_EMBED_DESCRIPTION) > 0)

    def test_afk_menu_title(self):
        self.assertIsInstance(config.AFK_MENU_TITLE, str)
        self.assertTrue(len(config.AFK_MENU_TITLE) > 0)

    def test_afk_menu_no_afk(self):
        self.assertIsInstance(config.AFK_MENU_NO_AFK, str)
        self.assertTrue(len(config.AFK_MENU_NO_AFK) > 0)

    def test_afk_menu_total(self):
        self.assertIsInstance(config.AFK_MENU_TOTAL, str)
        self.assertTrue(len(config.AFK_MENU_TOTAL) > 0)

    def test_afk_modal_title(self):
        self.assertIsInstance(config.AFK_MODAL_TITLE, str)
        self.assertTrue(len(config.AFK_MODAL_TITLE) > 0)

    def test_afk_modal_reason_label(self):
        self.assertIsInstance(config.AFK_MODAL_REASON_LABEL, str)
        self.assertTrue(len(config.AFK_MODAL_REASON_LABEL) > 0)

    def test_afk_modal_duration_label(self):
        self.assertIsInstance(config.AFK_MODAL_DURATION_LABEL, str)
        self.assertTrue(len(config.AFK_MODAL_DURATION_LABEL) > 0)

    def test_afk_buttons(self):
        self.assertIsInstance(config.AFK_BUTTON_LEAVE, str)
        self.assertIsInstance(config.AFK_BUTTON_RETURN, str)
        self.assertIsInstance(config.AFK_BUTTON_REFRESH, str)
        self.assertIsInstance(config.AFK_BUTTON_STAY, str)
        self.assertTrue(len(config.AFK_BUTTON_LEAVE) > 0)
        self.assertTrue(len(config.AFK_BUTTON_RETURN) > 0)
        self.assertTrue(len(config.AFK_BUTTON_REFRESH) > 0)
        self.assertTrue(len(config.AFK_BUTTON_STAY) > 0)

    def test_afk_reason_default(self):
        self.assertIsInstance(config.AFK_REASON_DEFAULT, str)
        self.assertTrue(len(config.AFK_REASON_DEFAULT) > 0)

    def test_afk_return_messages(self):
        self.assertIsInstance(config.AFK_RETURN_CONFIRM, str)
        self.assertIsInstance(config.AFK_RETURN_SUCCESS, str)
        self.assertIsInstance(config.AFK_RETURN_STAY, str)
        self.assertIsInstance(config.AFK_RETURN_ERROR, str)
        self.assertIsInstance(config.AFK_NOT_AFK, str)
        self.assertIsInstance(config.AFK_INVALID_USER, str)

    def test_afk_stats_messages(self):
        self.assertIsInstance(config.AFK_STATS_TITLE, str)
        self.assertIsInstance(config.AFK_STATS_NO_DATA, str)
        self.assertIsInstance(config.AFK_STATS_TOTAL, str)
        self.assertIsInstance(config.AFK_STATS_TOTAL_TIME, str)
        self.assertIsInstance(config.AFK_STATS_LONGEST, str)

    def test_afk_auto_reply(self):
        self.assertIsInstance(config.AFK_AUTO_REPLY, str)
        self.assertIn("{mention}", config.AFK_AUTO_REPLY)
        self.assertIn("{reason}", config.AFK_AUTO_REPLY)
        self.assertIn("{duration}", config.AFK_AUTO_REPLY)

    def test_afk_cooldown(self):
        self.assertIsInstance(config.AFK_COOLDOWN_SECONDS, int)
        self.assertGreater(config.AFK_COOLDOWN_SECONDS, 0)

    def test_afk_nick_prefix(self):
        self.assertIsInstance(config.AFK_NICK_PREFIX, str)
        self.assertEqual(config.AFK_NICK_PREFIX, "[AFK] ")


class TestValidate(unittest.TestCase):
    def test_current_config_valid(self):
        self.assertEqual(config.validate(), [])

    def test_catches_long_label(self):
        bad_fields = [("x" * 46, "p", True, 100)]
        with patch("config.RP_FIELDS", bad_fields):
            errors = config.validate()
        self.assertTrue(any("45" in e for e in errors))

    def test_catches_too_many_fields(self):
        bad_fields = [("f", "p", True, 100)] * 6
        with patch("config.CAPT_FIELDS", bad_fields):
            errors = config.validate()
        self.assertTrue(any("5" in e for e in errors))

    def test_modal_labels_fit_discord_limit(self):
        for fields in (config.RP_FIELDS, config.CAPT_FIELDS):
            for label, *_ in fields:
                self.assertLessEqual(len(label), 45, label)


if __name__ == "__main__":
    unittest.main()
