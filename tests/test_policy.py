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
