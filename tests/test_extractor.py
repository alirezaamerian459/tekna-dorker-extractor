import unittest

from persian_comment_crawler.extractor import extract_page


HTML = """
<html><body>
  <article><h1>Story</h1><p>Article body.</p></article>
  <section id="comments">
    <article class="comment" id="comment-11"><div class="comment-body">این یک دیدگاه واقعی است.</div><time datetime="2026-01-02">today</time></article>
    <article class="comment" id="comment-12"><div class="comment-content">A useful English reply</div></article>
  </section>
  <a href="/next">next</a>
</body></html>
"""


class ExtractorTests(unittest.TestCase):
    def test_extracts_persian_article_body_and_metadata(self):
        body = "این گزارش درباره وضعیت تازه فناوری در ایران است و جزئیات مهمی را برای خوانندگان توضیح می‌دهد. " * 5
        html = f'''<html><head><meta property="og:title" content="گزارش فناوری امروز"><meta property="article:published_time" content="2026-10-28"></head><body><article><h1>گزارش فناوری امروز</h1><p>{body}</p><section id="comments"><p>این نظر نباید داخل مقاله بیاید.</p></section></article></body></html>'''
        result = extract_page(html, "https://example.ir/news/1")
        self.assertIsNotNone(result.article)
        assert result.article is not None
        self.assertEqual(result.article.title, "گزارش فناوری امروز")
        self.assertNotIn("این نظر نباید", result.article.body)
        self.assertEqual(result.article.publication_date, "2026-10-28")

    def test_extracts_schema_article(self):
        body = "این متن ساختاریافته یک مقاله فارسی کامل است که باید به عنوان محتوای مقاله ذخیره شود. " * 5
        html = f'''<script type="application/ld+json">{{"@type":"NewsArticle","headline":"خبر ساختاریافته","articleBody":"{body}","datePublished":"2026-10-27"}}</script>'''
        result = extract_page(html, "https://example.ir/news/2")
        self.assertIsNotNone(result.article)
        assert result.article is not None
        self.assertEqual(result.article.method, "article-schema.org-jsonld")
        self.assertEqual(result.article.title, "خبر ساختاریافته")

    def test_extracts_persian_comments_and_links(self):
        result = extract_page(HTML, "https://example.org/post")
        self.assertEqual(len(result.comments), 1)
        self.assertEqual(result.comments[0].text, "این یک دیدگاه واقعی است.")
        self.assertEqual(result.comments[0].platform_id, "comment-11")
        self.assertIn("https://example.org/next", result.links)

    def test_extracts_schema_comment(self):
        html = '<script type="application/ld+json">{"@type":"Comment","text":"این نظر از داده ساختاریافته است"}</script>'
        result = extract_page(html, "https://example.org/post")
        self.assertEqual(result.comments[0].method, "schema.org-jsonld")

    def test_extracts_nested_app_reviews_but_not_ratings(self):
        html = """
        <script type="application/ld+json">
        {"@type":"SoftwareApplication","aggregateRating":{"ratingValue":4.3},
         "review":[{"@type":"Review","reviewBody":"این برنامه خیلی کاربردی است"},
                    {"@type":"Review","reviewBody":"۳.۹ از ۵"}]}
        </script>
        """
        result = extract_page(html, "https://myket.ir/app/com.example")
        self.assertEqual([item.text for item in result.comments], ["این برنامه خیلی کاربردی است"])

    def test_ignores_comment_submission_form_labels(self):
        html = """
        <section id="comments"><form id="commentform">
          <label>دیدگاهتان را بنویسید</label><label>نشانی ایمیل شما منتشر نخواهد شد</label>
        </form></section>
        """
        result = extract_page(html, "https://example.org/post")
        self.assertEqual(result.comments, ())

    def test_ignores_comment_controls_and_metadata(self):
        html = """
        <section id="comments">
          <article class="comment">
            <a class="comment-reply-link">پاسخ</a>
            <time>2 هفته پیش</time>
            <span class="comment-count">3 0 پاسخ 6 ماه پیش</span>
            <div class="comment-content">این یک نظر واقعی و مفید است.</div>
          </article>
        </section>
        """
        result = extract_page(html, "https://example.org/post")
        self.assertEqual([item.text for item in result.comments], ["این یک نظر واقعی و مفید است."])

    def test_ignores_first_comment_placeholder(self):
        html = '<section id="comments"><div class="comment">اولین دیدگاه را شما ثبت کنید! دیدگاه خود را بنویسید</div></section>'
        result = extract_page(html, "https://example.org/post")
        self.assertEqual(result.comments, ())

    def test_extracts_comment_body_from_tabnak_style_wrapper(self):
        html = """
        <div id="comment-23087827" class="height-card">
          <div class="ribbon ribbon-bookmark">کاربر مهمان -&gt; 1405/07/29</div>
          <a class="reply-link">پاسخ</a>
          <p class="ribbon-content">این متن واقعی دیدگاه باید بدون سربرگ ذخیره شود.</p>
        </div>
        """
        result = extract_page(html, "https://example.ir/news/1")
        self.assertEqual([item.text for item in result.comments], ["این متن واقعی دیدگاه باید بدون سربرگ ذخیره شود."])
