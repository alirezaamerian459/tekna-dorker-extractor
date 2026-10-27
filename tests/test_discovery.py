import unittest
from unittest.mock import patch

from persian_comment_crawler.config import CrawlConfig
from persian_comment_crawler.discovery import BingSearchProvider, CommonCrawlProvider, TrancoProvider, discovery_candidates


class DiscoveryTests(unittest.TestCase):
    def test_ir_search_filters_non_ir_results(self):
        provider = BingSearchProvider(api_key="secret")
        with patch.object(provider, "search", return_value=["https://example.ir/a", "https://example.com/b", "https://sub.example.ir/c"]):
            self.assertEqual(provider.search_ir('"نظرات کاربران"'), ["https://example.ir/a", "https://sub.example.ir/c"])

    def test_api_secret_is_redacted_from_session_config(self):
        raw = CrawlConfig(search_api_key="secret").to_json()
        self.assertNotIn("secret", raw)
        self.assertIn("[configured]", raw)

    def test_common_crawl_provider_filters_to_ir(self):
        provider = CommonCrawlProvider()
        provider.latest_index = lambda: "CC-MAIN-test"  # type: ignore[method-assign]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b'{"url":"https://one.ir/a"}\n{"url":"https://example.com/b"}\n{"url":"https://two.ir/c"}'

        with patch("persian_comment_crawler.discovery.urllib.request.urlopen", return_value=Response()):
            self.assertEqual(provider.discover_ir(), ["https://one.ir/a", "https://two.ir/c"])

    def test_tranco_provider_returns_ranked_ir_domains(self):
        provider = TrancoProvider()

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self.payload

            def __iter__(self):
                return iter([b"1,example.com\r\n", b"2,news.ir\r\n", b"3,shop.ir\r\n"])

        with patch("persian_comment_crawler.discovery.urllib.request.urlopen", side_effect=[Response(b'{"download":"https://tranco.test/list.csv"}'), Response(b"")]):
            self.assertEqual(provider.discover_ir(10), ["https://news.ir/", "https://shop.ir/"])

    def test_discovery_prioritizes_article_links_and_skips_assets(self):
        html = """
        <a href="/tag/music">tag</a>
        <a href="/news/useful-story">story</a>
        <a href="/images/photo.jpg">image</a>
        """
        candidates = list(discovery_candidates("https://example.ir/", html, "text/html"))
        self.assertEqual(candidates, [("https://example.ir/tag/music", "internal-link", 80), ("https://example.ir/news/useful-story", "internal-link", 30)])
