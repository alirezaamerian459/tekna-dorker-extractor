import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from persian_comment_crawler.config import CrawlConfig
from persian_comment_crawler.engine import CrawlEngine
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
