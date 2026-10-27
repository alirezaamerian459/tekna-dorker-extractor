import tempfile
import unittest
from pathlib import Path

from persian_comment_crawler.config import CrawlConfig
from persian_comment_crawler.exporter import TxtExporter
from persian_comment_crawler.models import CommentRecord
from persian_comment_crawler.storage import SQLiteStore


class StorageExportTests(unittest.TestCase):
    def test_queue_prunes_asset_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = CrawlConfig(data_root=Path(directory) / "data", minimum_free_disk_bytes=0)
            store = SQLiteStore(config.data_root, config)
            store.upsert_url("https://dl.example.ir/file", "internal-link")
            store.upsert_url("https://example.ir/article", "internal-link")
            self.assertEqual(store.skip_pending_host_prefixes(("dl",), "asset host"), 1)
            self.assertTrue(store.has_pending_urls())
            store.close()

    def test_comment_deduplication_and_domain_export(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config = CrawlConfig(data_root=tmp_path / "data", minimum_free_disk_bytes=0, export_rotation_bytes=300)
            store = SQLiteStore(config.data_root, config)
            store.start_session(config)
            record = CommentRecord("این دیدگاه ذخیره می‌شود", "https://example.ir/post", "example.ir", language_confidence=.9)
            self.assertTrue(store.save_comment(record))
            self.assertFalse(store.save_comment(record))
            files = TxtExporter(store, config).export("example.ir")
            self.assertTrue(files)
            self.assertTrue(files[0].read_text(encoding="utf-8").startswith("="))
            self.assertEqual(TxtExporter(store, config).export("example.ir", only_new=True), [])
            store.close()
