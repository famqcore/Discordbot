import importlib
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import config


def reload_config_with(env):
    """Перечитывает config с новым окружением и возвращает его после теста.

    _ENV_ERRORS и значения *_ID разбираются при импорте модуля,
    поэтому без reload изменение os.environ не видно.
    """
    return _ConfigReloader(env)


class _ConfigReloader:
    def __init__(self, env):
        self.env = env

    def __enter__(self):
        with patch.dict(os.environ, self.env):
            importlib.reload(config)

    def __exit__(self, *args):
        importlib.reload(config)
        return False


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
        self.assertIsInstance(config.LOG_CATEGORY_NAME, str)
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
        self.assertIn(config.FAMILY_NAME, config.DM_MESSAGE)

    def test_family_name_is_used_in_texts(self):
        """Название семьи задаётся одной переменной и подставляется в тексты."""
        self.assertIsInstance(config.FAMILY_NAME, str)
        self.assertTrue(config.FAMILY_NAME)
        self.assertIn(config.FAMILY_NAME, config.FAMQCORE_EMBED_TITLE)
        self.assertIn(config.FAMILY_NAME, config.TICKETS_CATEGORY_NAME)

    def test_log_sections_cover_all_keys(self):
        keys = {
            config.LOG_KEY_TICKETS,
            config.LOG_KEY_DECISIONS,
            config.LOG_KEY_AFK,
            config.LOG_KEY_CALLS,
            config.LOG_KEY_STATS,
            config.LOG_KEY_ERRORS,
            config.LOG_KEY_AUDIT,
        }
        self.assertEqual(set(config.LOG_SECTION_NAMES), keys)
        self.assertIs(config.LOG_THREAD_NAMES, config.LOG_SECTION_NAMES)

    def test_error_ping_settings(self):
        self.assertIsInstance(config.LOG_ERROR_PING_ENABLED, bool)
        self.assertIn("{mentions}", config.LOG_ERROR_PING_TEXT)
        self.assertIn("correlation_id", config.LOG_ERRORS_GUIDE)

    def test_ticket_titles(self):
        self.assertIsInstance(config.TICKET_RP_TITLE, str)
        self.assertIsInstance(config.TICKET_CAPT_TITLE, str)
        self.assertTrue(len(config.TICKET_RP_TITLE) > 0)
        self.assertTrue(len(config.TICKET_CAPT_TITLE) > 0)

    def test_famqcore_embed(self):
        self.assertIsInstance(config.FAMQCORE_EMBED_TITLE, str)
        self.assertIsInstance(config.FAMQCORE_EMBED_DESCRIPTION, str)
        self.assertIn(config.FAMILY_NAME, config.FAMQCORE_EMBED_TITLE)

    def test_ticket_forms_are_structured(self):
        self.assertEqual(len(config.TICKET_FORMS), 2)
        for form in config.TICKET_FORMS:
            self.assertIsInstance(form, config.TicketForm)
            self.assertTrue(form.title)
            self.assertTrue(form.ticket_type)
            self.assertEqual(len(form.fields), 5)
            for field in form.fields:
                self.assertIsInstance(field, config.TicketField)
                self.assertIsInstance(field.label, str)
                self.assertIsInstance(field.placeholder, str)
                self.assertIsInstance(field.required, bool)
                self.assertIsInstance(field.max_length, int)

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

    def test_voice_call_button_cooldown(self):
        self.assertIsInstance(config.VOICE_CALL_BUTTON_COOLDOWN_SECONDS, int)
        self.assertGreater(config.VOICE_CALL_BUTTON_COOLDOWN_SECONDS, 0)


class TestValidate(unittest.TestCase):
    def test_current_config_valid(self):
        self.assertEqual(config.validate(), [])

    def test_catches_long_label(self):
        invalid_form = replace(
            config.RP_FORM,
            fields=(config.TicketField("x" * 46, "p", True, 100),),
        )
        with patch("config.TICKET_FORMS", (invalid_form, config.CAPT_FORM)):
            errors = config.validate()
        self.assertTrue(any("45" in error for error in errors))

    def test_catches_too_many_fields(self):
        invalid_form = replace(
            config.CAPT_FORM,
            fields=(config.TicketField("f", "p", True, 100),) * 6,
        )
        with patch("config.TICKET_FORMS", (config.RP_FORM, invalid_form)):
            errors = config.validate()
        self.assertTrue(any("5" in error for error in errors))

    def test_modal_labels_fit_discord_limit(self):
        for form in config.TICKET_FORMS:
            for field in form.fields:
                self.assertLessEqual(len(field.label), config.DISCORD_LABEL_MAX_LENGTH, field.label)

    def test_catches_text_input_length_beyond_discord_limit(self):
        invalid_form = replace(
            config.RP_FORM,
            fields=(
                config.TicketField(
                    "Поле",
                    "Подсказка",
                    True,
                    config.DISCORD_TEXT_INPUT_MAX_LENGTH + 1,
                ),
            ),
        )
        with patch("config.TICKET_FORMS", (invalid_form, config.CAPT_FORM)):
            errors = config.validate()
        self.assertTrue(any("max_length" in error for error in errors))

    def test_catches_too_small_rate_limit_storage(self):
        with patch("config.RATELIMIT_MAX_ENTRIES", config.MIN_RATELIMIT_ENTRIES - 1):
            errors = config.validate()
        self.assertTrue(any("RATELIMIT_MAX_ENTRIES" in error for error in errors))


class TestEnvParsing(unittest.TestCase):
    """Разбор .env: мусорные ID — ошибка старта, а не молчаливый None."""

    def test_malformed_role_id_is_config_error(self):
        with reload_config_with({"ROLE_RECRUITER_ID": "не-цифры"}):
            errors = config.validate()
        self.assertTrue(any("ROLE_RECRUITER_ID" in e for e in errors))

    def test_malformed_voice_id_is_config_error(self):
        with reload_config_with({"VOICE_CHANNEL_IDS": "123,abc,456"}):
            errors = config.validate()
        self.assertTrue(any("VOICE_CHANNEL_IDS" in e for e in errors))

    def test_thread_ids_without_channel_id_is_config_error(self):
        # ветка вне своего канала: приватность нечем гарантировать
        with reload_config_with({"LOG_THREAD_AFK_ID": "123456789012345678"}):
            errors = config.validate()
        self.assertTrue(any("LOG_CHANNEL_ID" in e for e in errors))

    def test_retention_days_default(self):
        self.assertEqual(config.TICKET_RETENTION_DAYS, 180)
        self.assertGreaterEqual(config.RETENTION_CHECK_SECONDS, 60)

    def test_retention_days_zero_is_config_error(self):
        with reload_config_with({"TICKET_RETENTION_DAYS": "0"}):
            errors = config.validate()
        self.assertTrue(any("TICKET_RETENTION_DAYS" in e for e in errors))

    def test_malformed_retention_interval_is_config_error(self):
        with reload_config_with({"RETENTION_CHECK_SECONDS": "ежедневно"}):
            errors = config.validate()
        self.assertTrue(any("RETENTION_CHECK_SECONDS" in e for e in errors))

    def test_validate_clean_with_only_token(self):
        errors = config.validate()
        self.assertEqual(errors, [])

    def test_token_whitespace_is_stripped(self):
        with reload_config_with({"TOKEN": "  secret-token\n"}):
            self.assertEqual(config.TOKEN, "secret-token")

    def test_whitespace_only_token_is_missing(self):
        with reload_config_with({"TOKEN": " \t\n "}):
            self.assertIsNone(config.TOKEN)
            errors = config.validate(require_token=True)
        self.assertTrue(any("TOKEN не задан" in error for error in errors))


class TestNameFallbackFlag(unittest.TestCase):
    def test_fallback_disabled_by_default(self):
        self.assertFalse(config.ALLOW_NAME_FALLBACK)

    def test_fallback_enabled_by_env(self):
        with reload_config_with({"ALLOW_NAME_FALLBACK": "true"}):
            self.assertTrue(config.ALLOW_NAME_FALLBACK)


if __name__ == "__main__":
    unittest.main()
