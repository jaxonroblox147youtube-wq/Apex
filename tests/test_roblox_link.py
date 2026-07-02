import os
import tempfile
import unittest

os.environ.setdefault("DISCORD_TOKEN", "dummy")

import bot


class RobloxLinkStorageTests(unittest.TestCase):
    def test_store_and_load_linked_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "roblox_links.json")
            original_path = bot.ROBLOX_LINKS_FILE
            bot.ROBLOX_LINKS_FILE = path
            try:
                bot._store_roblox_link(12345, {"access_token": "abc123", "refresh_token": "xyz789"})
                data = bot._load_roblox_link(12345)
            finally:
                bot.ROBLOX_LINKS_FILE = original_path

            self.assertEqual(data.get("access_token"), "abc123")
            self.assertEqual(data.get("refresh_token"), "xyz789")

    def test_uses_absolute_path_for_storage_file(self):
        self.assertTrue(os.path.isabs(bot.ROBLOX_LINKS_FILE))

    def test_resolves_redirect_uri_from_replit_url(self):
        original = os.environ.get("ROBLOX_REDIRECT_URI")
        os.environ.pop("ROBLOX_REDIRECT_URI", None)
        os.environ["REPLIT_URL"] = "https://example.repl.co/"
        try:
            self.assertEqual(
                bot._get_roblox_redirect_uri(),
                "https://example.repl.co/api/roblox/callback",
            )
        finally:
            if original is None:
                os.environ.pop("ROBLOX_REDIRECT_URI", None)
            else:
                os.environ["ROBLOX_REDIRECT_URI"] = original
            os.environ.pop("REPLIT_URL", None)


if __name__ == "__main__":
    unittest.main()
