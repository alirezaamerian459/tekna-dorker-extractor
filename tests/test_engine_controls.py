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
