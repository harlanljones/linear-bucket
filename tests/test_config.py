import unittest

from parent_progress_sync.config import DEFAULT_API_URL, Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(ConfigError):
            Config.from_env({})
        with self.assertRaises(ConfigError):
            Config.from_env({"LINEAR_API_KEY": "   "})

    def test_defaults(self):
        config = Config.from_env({"LINEAR_API_KEY": "lin_api_test"})

        self.assertEqual(config.api_url, DEFAULT_API_URL)
        self.assertIsNone(config.team_key)
        self.assertEqual(config.page_size, 50)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.label_group, "Parent progress")
        self.assertFalse(config.bootstrap_labels)
        self.assertFalse(config.cleanup_legacy_prefixes)

    def test_reads_overrides(self):
        config = Config.from_env(
            {
                "LINEAR_API_KEY": "lin_api_test",
                "LINEAR_API_URL": "https://example.test/graphql",
                "LINEAR_TEAM_KEY": "ENG",
                "LINEAR_PAGE_SIZE": "100",
                "LINEAR_MAX_RETRIES": "2",
                "LINEAR_DRY_RUN": "true",
                "LINEAR_LABEL_GROUP": "Progress",
                "LINEAR_BOOTSTRAP_LABELS": "1",
                "LINEAR_CLEANUP_LEGACY_PREFIXES": "yes",
            }
        )

        self.assertEqual(config.api_url, "https://example.test/graphql")
        self.assertEqual(config.team_key, "ENG")
        self.assertEqual(config.page_size, 100)
        self.assertEqual(config.max_retries, 2)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.label_group, "Progress")
        self.assertTrue(config.bootstrap_labels)
        self.assertTrue(config.cleanup_legacy_prefixes)

    def test_rejects_invalid_values(self):
        base = {"LINEAR_API_KEY": "lin_api_test"}
        with self.assertRaises(ConfigError):
            Config.from_env({**base, "LINEAR_PAGE_SIZE": "abc"})
        with self.assertRaises(ConfigError):
            Config.from_env({**base, "LINEAR_PAGE_SIZE": "500"})
        with self.assertRaises(ConfigError):
            Config.from_env({**base, "LINEAR_DRY_RUN": "maybe"})


if __name__ == "__main__":
    unittest.main()
