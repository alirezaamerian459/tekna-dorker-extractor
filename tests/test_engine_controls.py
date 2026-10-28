import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from persian_comment_crawler.config import CrawlConfig
from persian_comment_crawler.engine import CrawlEngine
from persian_comment_crawler.fetcher import FetchResult
from persian_comment_crawler.models import CrawlState
from persian_comment_crawler.storage import SQLiteStore


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        time.sleep(0.15)
        body = '<section id="comments"><article class="comment"><div class="comment-body">این يک نظر آزمایشی است</div></article></section>'.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class EngineControlTests(unittest.TestCase):
    def test_article_mode_uses_editorial_priority_profile(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", auto_ir_discovery=True, minimum_free_disk_bytes=0, content_mode="articles")
            store = SQLiteStore(config.data_root, config)
            engine = CrawlEngine(config, store)
            engine.tranco_provider.priority_for_url = lambda _url, fallback: min(fallback, 15)  # type: ignore[method-assign]
            editorial = engine._priority_for_url("https://example.ir/news/123", 50)
            catalog = engine._priority_for_url("https://cafebazaar.ir/app/com.example", 50)
            self.assertLess(editorial, catalog)
            store.close()

    def test_article_mode_filters_non_editorial_sitemap_fanout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", auto_ir_discovery=False, minimum_free_disk_bytes=0, content_mode="articles")
            store = SQLiteStore(config.data_root, config)
            engine = CrawlEngine(config, store)
            candidates = [
                ("https://news.example.ir/news/1", "sitemap-or-feed", 20),
                ("https://news.example.ir/tag/politics", "sitemap-or-feed", 20),
                ("https://news.example.ir/assets/file.pdf", "sitemap-or-feed", 20),
            ]
            selected = engine._select_candidates("https://news.example.ir/sitemap.xml", "text/xml", candidates)
            self.assertEqual([row[0] for row in selected], ["https://news.example.ir/news/1"])
            store.close()

    def test_article_mode_persists_article_records_instead_of_comments(self):
        import tempfile
        body = "این گزارش فارسی برای آزمون حالت مقاله نوشته شده و اطلاعات مفیدی درباره موضوع ارائه می‌کند. " * 5
        html = f"<html><head><title>گزارش آزمایشی</title></head><body><article><h1>گزارش آزمایشی</h1><p>{body}</p></article></body></html>"
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", auto_ir_discovery=False, minimum_free_disk_bytes=0, domain_cooldown_seconds=0, content_mode="articles")
            store = SQLiteStore(config.data_root, config)
            store.upsert_url("https://example.ir/news/1", "seed", 1)
            row = store.claim_url()
            engine = CrawlEngine(config, store)
            engine.fetcher.fetch = lambda url: FetchResult(url, 200, "text/html", html.encode("utf-8"), None)  # type: ignore[method-assign]
            with patch.object(engine, "_allowed_by_robots", return_value=True):
                engine._process_url(row["url"])
            self.assertEqual(store.count("articles"), 1)
            self.assertEqual(store.count("comments"), 0)
            store.close()

    def test_adaptive_host_pacing_speeds_clean_hosts_and_backs_off(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", auto_ir_discovery=False, minimum_free_disk_bytes=0)
            store = SQLiteStore(config.data_root, config)
            engine = CrawlEngine(config, store)
            clean = FetchResult("https://example.ir/", 200, "text/html", b"", None)
            engine._update_domain_pacing("example.ir", clean)
            self.assertAlmostEqual(engine._domain_delay("example.ir"), 1.6)
            blocked = FetchResult("https://example.ir/", 429, "", b"", "HTTP Error 429")
            engine._update_domain_pacing("example.ir", blocked)
            self.assertAlmostEqual(engine._domain_delay("example.ir"), 3.2)
            store.close()

    def test_catalog_and_sitemap_links_bypass_ordinary_link_cap(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", auto_ir_discovery=False, minimum_free_disk_bytes=0, max_links_per_page=30)
            store = SQLiteStore(config.data_root, config)
            engine = CrawlEngine(config, store)
            candidates = [(f"https://www.digikala.com/product/dkp-{index}", "internal-link", 5) for index in range(40)]
            candidates.extend((f"https://example.ir/ordinary/{index}", "internal-link", 50) for index in range(40))
            selected = engine._select_candidates("https://www.digikala.com/", "text/html", candidates)
            self.assertEqual(len(selected), 70)
            sitemap_candidates = [(f"https://dkstatics-public.digikala.com/digikala-site-map/part-{index}.gz", "sitemap-or-feed", 7) for index in range(100)]
            self.assertEqual(len(engine._select_candidates("https://www.digikala.com/sitemap.xml", "text/xml", sitemap_candidates)), 100)
            store.close()

    def test_local_fixture_crawl_and_stop_checkpoint(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.subTest("crawl"):
                import tempfile
                with tempfile.TemporaryDirectory() as directory:
                    config = CrawlConfig(data_root=Path(directory) / "data", seed_urls=[f"http://127.0.0.1:{server.server_port}/"], auto_ir_discovery=False, minimum_free_disk_bytes=0, domain_cooldown_seconds=0)
                    store = SQLiteStore(config.data_root, config)
                    engine = CrawlEngine(config, store)
                    with patch("persian_comment_crawler.fetcher.validate_public_url", side_effect=lambda url: url), patch.object(engine, "_allowed_by_robots", return_value=True):
                        engine.start()
                        time.sleep(0.03)
                        engine.stop()
                        engine.wait(3)
                    self.assertIn(engine.state, {CrawlState.STOPPED, CrawlState.COMPLETED})
                    self.assertEqual(store.count("comments"), 1)
                    self.assertTrue((config.data_root / "Data" / "Checkpoints" / "latest.json").exists())
                    store.close()
        finally:
            server.shutdown()
            server.server_close()
