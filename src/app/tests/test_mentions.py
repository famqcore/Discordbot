import unittest

from utils.mentions import DEFAULT_ALLOWED_MENTIONS, escape_user_text, mentions_for


class TestEscapeUserText(unittest.TestCase):
    def test_everyone_neutralized(self):
        self.assertNotIn("@everyone", escape_user_text("@everyone срочно"))

    def test_here_neutralized(self):
        self.assertNotIn("@here", escape_user_text("привет @here"))

    def test_user_mention_neutralized(self):
        text = escape_user_text("пингую <@123456789012345678>")
        self.assertNotIn("<@123456789012345678>", text)

    def test_old_style_user_mention_neutralized(self):
        text = escape_user_text("<@!123456789012345678>")
        self.assertNotIn("<@!123456789012345678>", text)

    def test_role_mention_neutralized(self):
        text = escape_user_text("роль <@&123456789012345678>")
        self.assertNotIn("<@&123456789012345678>", text)

    def test_normal_text_untouched(self):
        text = "Отошёл по делам, вернусь к 18:00"
        self.assertEqual(escape_user_text(text), text)

    def test_empty_and_none_safe(self):
        self.assertEqual(escape_user_text(""), "")
        self.assertEqual(escape_user_text(None), "")


class TestMentionsFor(unittest.TestCase):
    def test_everyone_and_roles_disabled(self):
        allowed = mentions_for()
        self.assertFalse(allowed.everyone)
        self.assertEqual(allowed.roles, [])
        self.assertEqual(allowed.users, [])
        self.assertFalse(allowed.replied_user)

    def test_addressed_users_only(self):
        user = object()
        allowed = mentions_for(users=[user])
        self.assertEqual(allowed.users, [user])
        self.assertEqual(allowed.roles, [])
        self.assertFalse(allowed.everyone)

    def test_addressed_roles_only_when_service_ping(self):
        role = object()
        allowed = mentions_for(roles=[role])
        self.assertEqual(allowed.roles, [role])
        self.assertEqual(allowed.users, [])
        self.assertFalse(allowed.everyone)


class TestDefaultClientPolicy(unittest.TestCase):
    def test_mass_pings_disabled_globally(self):
        self.assertFalse(DEFAULT_ALLOWED_MENTIONS.everyone)
        self.assertFalse(DEFAULT_ALLOWED_MENTIONS.roles)
        self.assertFalse(DEFAULT_ALLOWED_MENTIONS.replied_user)


if __name__ == "__main__":
    unittest.main()
