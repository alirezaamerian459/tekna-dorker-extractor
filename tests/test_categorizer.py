import unittest

from persian_comment_crawler.categorizer import classify_article


class CategorizerTests(unittest.TestCase):
    def test_primary_category_uses_title_and_body_signals(self):
        result = classify_article(
            "گزارش تازه درباره هوش مصنوعی و فناوری",
            "این گزارش درباره نرم افزار، اینترنت و توسعه فناوری در ایران است. " * 8,
            "https://example.ir/technology/story",
        )
        self.assertEqual(result.primary_category, "technology")
        self.assertIn("technology", result.categories)
        self.assertGreater(result.confidence, 0.5)
        self.assertGreater(result.quality_score, 0.5)

    def test_multi_label_and_other_fallback(self):
        mixed = classify_article(
            "سرمایه گذاری در استارتاپ های فناوری",
            "این شرکت تازه با یک محصول فناوری و برنامه کسب و کار جدید وارد بازار شد. " * 6,
            "https://example.ir/business/tech",
        )
        self.assertIn("business", mixed.categories)
        self.assertIn("technology", mixed.categories)
        unknown = classify_article("آسمان آبی امروز", "متن کوتاه و عمومی.", "https://example.ir/story")
        self.assertEqual(unknown.primary_category, "other")

