import unittest

from persian_comment_crawler.persian import classify_persian, normalize_persian


class PersianTests(unittest.TestCase):
    def test_normalizes_arabic_variants_and_zero_width_marks(self):
        self.assertEqual(normalize_persian("يك‏ كتاب"), "یک کتاب")

    def test_classifies_persian_mixed_text(self):
        result = classify_persian("این یک دیدگاه خوب است 👍 with English")
        self.assertEqual(result.language, "fa")
        self.assertGreater(result.confidence, 0.45)

    def test_does_not_classify_latin_as_persian(self):
        self.assertEqual(classify_persian("This is an English comment").language, "non-fa")
