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

    def test_ignores_comment_submission_form_labels(self):
        html = """
        <section id="comments"><form id="commentform">
          <label>دیدگاهتان را بنویسید</label><label>نشانی ایمیل شما منتشر نخواهد شد</label>
        </form></section>
        """
        result = extract_page(html, "https://example.org/post")
        self.assertEqual(result.comments, ())
