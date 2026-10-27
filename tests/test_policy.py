import unittest

from persian_comment_crawler.policy import UnsafeUrl, normalize_url


class PolicyTests(unittest.TestCase):
    def test_normalizes_and_strips_fragment(self):
        self.assertEqual(normalize_url("HTTPS://Example.COM/a#fragment"), "https://example.com/a")

    def test_rejects_non_web_scheme_and_credentials(self):
        with self.assertRaises(UnsafeUrl):
            normalize_url("file:///etc/passwd")
        with self.assertRaises(UnsafeUrl):
            normalize_url("https://user:pass@example.com")

    def test_quotes_unicode_paths_for_http_requests(self):
        self.assertEqual(normalize_url("https://example.ir/جلسه-شماره-۹۰.pdf"), "https://example.ir/%D8%AC%D9%84%D8%B3%D9%87-%D8%B4%D9%85%D8%A7%D8%B1%D9%87-%DB%B9%DB%B0.pdf")
