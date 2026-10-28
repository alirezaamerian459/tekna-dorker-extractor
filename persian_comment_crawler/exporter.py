from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from sqlite3 import Row

from .config import CrawlConfig
from .storage import SQLiteStore, StorageBudgetExceeded


def safe_domain_name(domain: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", domain.strip().lower()).strip("._")
    return cleaned[:180] or "unknown-domain"


class TxtExporter:
    def __init__(self, store: SQLiteStore, config: CrawlConfig) -> None:
        self.store = store
        self.config = config

    def export(self, domain: str | None = None, only_new: bool = False) -> list[Path]:
        rows = self.store.comments_for_domain(domain)
        if only_new:
            rows = [row for row in rows if not self._was_exported(row["record_id"])]
        grouped: dict[str, list[Row]] = {}
        for row in rows:
            grouped.setdefault(row["domain"], []).append(row)
        outputs: list[Path] = []
        for row_domain, records in grouped.items():
            outputs.extend(self._export_domain(row_domain, records))
        return outputs

    def _was_exported(self, record_id: str) -> bool:
        return self.store.was_exported(record_id)

    def _export_domain(self, domain: str, records: list[Row]) -> list[Path]:
        directory = self.store.comments_dir / safe_domain_name(domain)
        directory.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        written_chunks: list[tuple[Path, list[Row]]] = []
        rotation = self.config.export_rotation_bytes
        part = 1
        current: list[str] = []
        current_rows: list[Row] = []
        current_size = 0

        def flush(lines: list[str], part_number: int) -> Path:
            payload = "\n".join(lines) + "\n"
            encoded = payload.encode("utf-8")
            self.store.budget.ensure_capacity(len(encoded))
            target = directory / f"comments_{part_number:03d}.txt"
            if target.exists():
                target = directory / f"comments_{part_number:03d}_{len(outputs) + 1:03d}.txt"
            fd, temp_name = tempfile.mkstemp(prefix="export-", suffix=".tmp", dir=self.store.temp_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return target

        for row in records:
            block = (
                "=" * 72 + "\n"
                f"Record ID: {row['record_id']}\n"
                f"Source URL: {row['source_url']}\n"
                f"Publication date: {row['publication_date'] or 'unknown'}\n"
                f"Comment:\n{row['text']}\n"
            )
            block_size = len(block.encode("utf-8"))
            if current and current_size + block_size > rotation:
                target = flush(current, part)
                outputs.append(target)
                written_chunks.append((target, current_rows))
                part += 1
                current, current_rows, current_size = [], [], 0
            current.append(block)
            current_rows.append(row)
            current_size += block_size
        if current:
            target = flush(current, part)
            outputs.append(target)
            written_chunks.append((target, current_rows))

        for path, chunk_rows in written_chunks:
            with self.store.transaction() as db:
                db.execute("INSERT INTO exports(exported_at,domain,output_path,comment_count,bytes_written) VALUES(?,?,?,?,?)", (self._now(), domain, str(path), len(chunk_rows), path.stat().st_size))
                for row in chunk_rows:
                    db.execute("INSERT OR IGNORE INTO exported_records(record_id,exported_at,output_path) VALUES(?,?,?)", (row["record_id"], self._now(), str(path)))
        return outputs

    @staticmethod
    def _now() -> str:
        from .models import utc_now
        return utc_now()


class ArticleTxtExporter:
    """Export extracted articles separately from the comment corpus."""

    def __init__(self, store: SQLiteStore, config: CrawlConfig) -> None:
        self.store = store
        self.config = config

    def export(self, domain: str | None = None, only_new: bool = False) -> list[Path]:
        rows = self.store.articles_for_domain(domain)
        if only_new:
            rows = [row for row in rows if not self.store.was_article_exported(row["record_id"])]
        grouped: dict[str, list[Row]] = {}
        for row in rows:
            grouped.setdefault(row["domain"], []).append(row)
        outputs: list[Path] = []
        for row_domain, records in grouped.items():
            outputs.extend(self._export_domain(row_domain, records))
        return outputs

    def _export_domain(self, domain: str, records: list[Row]) -> list[Path]:
        directory = self.store.articles_dir / safe_domain_name(domain)
        directory.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        written_chunks: list[tuple[Path, list[Row]]] = []
        rotation = self.config.export_rotation_bytes
        part = 1
        current: list[str] = []
        current_rows: list[Row] = []
        current_size = 0

        def flush(lines: list[str], part_number: int) -> Path:
            payload = "\n".join(lines) + "\n"
            encoded = payload.encode("utf-8")
            self.store.budget.ensure_capacity(len(encoded))
            target = directory / f"articles_{part_number:03d}.txt"
            if target.exists():
                target = directory / f"articles_{part_number:03d}_{len(outputs) + 1:03d}.txt"
            fd, temp_name = tempfile.mkstemp(prefix="article-export-", suffix=".tmp", dir=self.store.temp_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return target

        for row in records:
            block = (
                "=" * 72 + "\n"
                f"Record ID: {row['record_id']}\n"
                f"Title: {row['title']}\n"
                f"Source URL: {row['source_url']}\n"
                f"Publication date: {row['publication_date'] or 'unknown'}\n"
                f"Author: {row['author'] or 'unknown'}\n"
                f"Article:\n{row['body']}\n"
            )
            block_size = len(block.encode("utf-8"))
            if current and current_size + block_size > rotation:
                target = flush(current, part)
                outputs.append(target)
                written_chunks.append((target, current_rows))
                part += 1
                current, current_rows, current_size = [], [], 0
            current.append(block)
            current_rows.append(row)
            current_size += block_size
        if current:
            target = flush(current, part)
            outputs.append(target)
            written_chunks.append((target, current_rows))

        for path, chunk_rows in written_chunks:
            with self.store.transaction() as db:
                db.execute("INSERT INTO article_exports(exported_at,domain,output_path,article_count,bytes_written) VALUES(?,?,?,?,?)", (self._now(), domain, str(path), len(chunk_rows), path.stat().st_size))
                for row in chunk_rows:
                    db.execute("INSERT OR IGNORE INTO exported_articles(record_id,exported_at,output_path) VALUES(?,?,?)", (row["record_id"], self._now(), str(path)))
        return outputs

    @staticmethod
    def _now() -> str:
        from .models import utc_now
        return utc_now()
