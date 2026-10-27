import unittest
from unittest.mock import patch

from persian_comment_crawler.config import CrawlConfig
from persian_comment_crawler.discovery import BingSearchProvider


class DiscoveryTests(unittest.TestCase):
    def test_ir_search_filters_non_ir_results(self):
        provider = BingSearchProvider(api_key="secret")
        with patch.object(provider, "search", return_value=["https://example.ir/a", "https://example.com/b", "https://sub.example.ir/c"]):
            self.assertEqual(provider.search_ir('"نظرات کاربران"'), ["https://example.ir/a", "https://sub.example.ir/c"])

    def test_api_secret_is_redacted_from_session_config(self):
        raw = CrawlConfig(search_api_key="secret").to_json()
        self.assertNotIn("secret", raw)
        self.assertIn("[configured]", raw)
