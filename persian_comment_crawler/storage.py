from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import urlsplit

from .config import CrawlConfig
from .models import CommentRecord, CrawlState, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS domains (
  domain TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  discovered_count INTEGER NOT NULL DEFAULT 0, comment_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS discovered_urls (
  url TEXT PRIMARY KEY, domain TEXT NOT NULL, discovery_source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 50,
  attempts INTEGER NOT NULL DEFAULT 0, discovered_at TEXT NOT NULL,
  visited_at TEXT, last_error TEXT, UNIQUE(url)
);
CREATE INDEX IF NOT EXISTS idx_urls_status_priority ON discovered_urls(status, priority, discovered_at);
CREATE TABLE IF NOT EXISTS crawl_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ended_at TEXT,
  state TEXT NOT NULL, config_json TEXT NOT NULL, storage_limit INTEGER NOT NULL,
  comments_count INTEGER NOT NULL DEFAULT 0, checkpoint_path TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS comments (
  record_id TEXT PRIMARY KEY, platform_id TEXT, normalized_text TEXT NOT NULL,
  text TEXT NOT NULL, domain TEXT NOT NULL, source_url TEXT NOT NULL,
  comment_url TEXT, publication_date TEXT, discovered_at TEXT NOT NULL,
  extraction_method TEXT NOT NULL, language_confidence REAL NOT NULL,
  source_context TEXT, UNIQUE(platform_id),
  UNIQUE(domain, source_url, normalized_text, source_context)
);
CREATE INDEX IF NOT EXISTS idx_comments_domain ON comments(domain);
CREATE TABLE IF NOT EXISTS extraction_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, attempted_at TEXT NOT NULL,
  method TEXT NOT NULL, comments_found INTEGER NOT NULL DEFAULT 0,
  success INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, exported_at TEXT NOT NULL, domain TEXT,
  output_path TEXT NOT NULL, comment_count INTEGER NOT NULL, bytes_written INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exported_records (
  record_id TEXT PRIMARY KEY, exported_at TEXT NOT NULL, output_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_accounting (
  session_id INTEGER PRIMARY KEY, bytes_written INTEGER NOT NULL DEFAULT 0,
  last_measured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS error_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  level TEXT NOT NULL, component TEXT NOT NULL, message TEXT NOT NULL
);
"""


class StorageBudgetExceeded(RuntimeError):
    pass


def directory_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class StorageBudget:
    def __init__(self, root: Path, limit_bytes: int, minimum_free_bytes: int) -> None:
        self.root = root
        self.limit_bytes = limit_bytes
        self.minimum_free_bytes = minimum_free_bytes
        self.safety_margin_bytes = min(20_000_000, max(4_096, limit_bytes // 20))

    def usage(self) -> int:
        return directory_size(self.root)

    def remaining(self) -> int:
        return max(0, self.limit_bytes - self.safety_margin_bytes - self.usage())

    def ensure_capacity(self, expected_bytes: int = 0) -> None:
        usage = self.usage()
        if usage + max(0, expected_bytes) + self.safety_margin_bytes > self.limit_bytes:
            raise StorageBudgetExceeded("the managed storage allowance has been reached")
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as exc:
            raise StorageBudgetExceeded(f"could not inspect free disk space: {exc}") from exc
        if free - max(0, expected_bytes) < self.minimum_free_bytes:
            raise StorageBudgetExceeded("minimum free disk-space safeguard reached")


class SQLiteStore:
    def __init__(self, data_root: Path, config: CrawlConfig) -> None:
        self.data_root = data_root
        self.comments_dir = data_root / "Data" / "Comments"
        self.database_dir = data_root / "Data" / "Database"
        self.logs_dir = data_root / "Data" / "Logs"
        self.checkpoints_dir = data_root / "Data" / "Checkpoints"
        self.temp_dir = data_root / "Data" / "Temp"
        for path in (self.comments_dir, self.database_dir, self.logs_dir, self.checkpoints_dir, self.temp_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.database_dir / "crawler.db"
        self.budget = StorageBudget(data_root, config.storage_limit_bytes, config.minimum_free_disk_bytes)
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.executescript(SCHEMA)
        self._connection.commit()
        self.session_id: int | None = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        self._connection.close()

    def start_session(self, config: CrawlConfig) -> int:
        with self.transaction() as db:
            cur = db.execute(
                "INSERT INTO crawl_sessions(started_at,state,config_json,storage_limit) VALUES(?,?,?,?)",
                (utc_now(), CrawlState.RUNNING.value, config.to_json(), config.session_limit_bytes),
            )
            self.session_id = int(cur.lastrowid)
            db.execute("INSERT OR REPLACE INTO storage_accounting(session_id,last_measured_at) VALUES(?,?)", (self.session_id, utc_now()))
        return self.session_id

    def set_session_state(self, state: CrawlState, reason: str | None = None) -> None:
        if self.session_id is None:
            return
        with self.transaction() as db:
            db.execute(
                "UPDATE crawl_sessions SET state=?, ended_at=CASE WHEN ? IN ('stopped','completed','error') THEN ? ELSE ended_at END, reason=? WHERE id=?",
                (state.value, state.value, utc_now(), reason, self.session_id),
            )

    def upsert_url(self, url: str, source: str, priority: int = 50) -> bool:
        return bool(self.upsert_urls(((url, source, priority),)))

    def upsert_urls(self, rows: Iterable[tuple[str, str, int]]) -> int:
        """Insert a batch of discovered URLs in one transaction."""
        values = list(rows)
        if not values:
            return 0
        now = utc_now()
        with self.transaction() as db:
            inserted = 0
            for url, source, priority in values:
                domain = (urlsplit(url).hostname or "").lower()
                cur = db.execute(
                    "INSERT OR IGNORE INTO discovered_urls(url,domain,discovery_source,priority,discovered_at) VALUES(?,?,?,?,?)",
                    (url, domain, source, priority, now),
                )
                db.execute("INSERT OR IGNORE INTO domains(domain,first_seen,last_seen) VALUES(?,?,?)", (domain, now, now))
                if cur.rowcount:
                    inserted += 1
                    db.execute("UPDATE domains SET discovered_count=discovered_count+1,last_seen=? WHERE domain=?", (now, domain))
            return inserted

    def claim_url(self, excluded_domains: tuple[str, ...] = ()) -> sqlite3.Row | None:
        with self.transaction() as db:
            if excluded_domains:
                placeholders = ",".join("?" for _ in excluded_domains)
                query = f"SELECT * FROM discovered_urls WHERE status='pending' AND domain NOT IN ({placeholders}) ORDER BY priority ASC, discovered_at ASC LIMIT 1"
                row = db.execute(query, excluded_domains).fetchone()
            else:
                row = db.execute("SELECT * FROM discovered_urls WHERE status='pending' ORDER BY priority ASC, discovered_at ASC LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE discovered_urls SET status='active',attempts=attempts+1 WHERE url=?", (row["url"],))
            return row

    def has_pending_urls(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM discovered_urls WHERE status='pending' LIMIT 1").fetchone() is not None

    def domain_page_count(self, domain: str) -> int:
        """Count claimed URLs for a domain, including active work."""
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM discovered_urls WHERE domain=? AND status IN ('active','done','error','skipped')",
                (domain,),
            ).fetchone()
            return int(row[0])

    def skip_pending_domain(self, domain: str, reason: str) -> int:
        """Stop queued expansion for a domain after its budget is exhausted."""
        with self.transaction() as db:
            cur = db.execute(
                "UPDATE discovered_urls SET status='skipped',visited_at=?,last_error=? WHERE domain=? AND status='pending'",
                (utc_now(), reason[:300], domain),
            )
            return int(cur.rowcount)

    def skip_pending_host_prefixes(self, prefixes: tuple[str, ...], reason: str) -> int:
        """Prune queued asset/CDN subdomains without deleting discovered data."""
        if not prefixes:
            return 0
        clauses: list[str] = []
        values: list[str] = []
        for prefix in prefixes:
            clauses.extend(("domain=?", "domain LIKE ?"))
            values.extend((prefix, f"{prefix}.%"))
        with self.transaction() as db:
            cur = db.execute(
                f"UPDATE discovered_urls SET status='skipped',visited_at=?,last_error=? WHERE status='pending' AND ({' OR '.join(clauses)})",
                (utc_now(), reason[:300], *values),
            )
            return int(cur.rowcount)

    def skip_pending_over_budget_domains(self, max_pages: int, reason: str) -> int:
        """Prune backlog for domains that already exhausted their page budget."""
        if max_pages <= 0:
            return 0
        with self.transaction() as db:
            cur = db.execute(
                """UPDATE discovered_urls SET status='skipped',visited_at=?,last_error=?
                   WHERE status='pending' AND domain IN (
                     SELECT domain FROM discovered_urls
                     WHERE status IN ('active','done','error','skipped')
                     GROUP BY domain HAVING COUNT(*) >= ?
                   )""",
                (utc_now(), reason[:300], max(1, int(max_pages))),
            )
            return int(cur.rowcount)

    def requeue_domain_limit_skips(self) -> int:
        """Restore URLs skipped by an older per-domain cap when that cap is disabled."""
        with self.transaction() as db:
            cur = db.execute(
                """UPDATE discovered_urls SET status='pending',visited_at=NULL,last_error=NULL
                   WHERE status='skipped' AND (
                     last_error='domain page limit reached' OR url IN (
                       SELECT DISTINCT url FROM extraction_attempts
                       WHERE method='domain-limit' AND error='domain page limit reached'
                     )
                   )"""
            )
            return int(cur.rowcount)

    def reprioritize_pending(self, scorer: Callable[[str, int], int]) -> int:
        """Update queued priorities without changing URLs or their sources."""
        with self.transaction() as db:
            rows = db.execute("SELECT url,priority FROM discovered_urls WHERE status='pending'").fetchall()
            updates = []
            for row in rows:
                priority = max(0, int(scorer(str(row["url"]), int(row["priority"]))))
                if priority != int(row["priority"]):
                    updates.append((priority, row["url"]))
            if updates:
                db.executemany("UPDATE discovered_urls SET priority=? WHERE url=? AND status='pending'", updates)
            return len(updates)

    def requeue_incomplete_urls(self, max_retries: int = 2) -> int:
        with self.transaction() as db:
            cur = db.execute("UPDATE discovered_urls SET status='pending' WHERE status='active' OR (status='error' AND attempts <= ?)", (max(0, max_retries),))
            return int(cur.rowcount)

    def complete_url(self, url: str, error: str | None = None, status: str | None = None) -> None:
        with self.transaction() as db:
            final_status = status or ("error" if error else "done")
            db.execute("UPDATE discovered_urls SET status=?,visited_at=?,last_error=? WHERE url=?", (final_status, utc_now(), error, url))

    def save_comment(self, record: CommentRecord, source_context: str = "") -> bool:
        normalized = record.text.strip()
        record_id = record.record_id or hashlib.sha256(
            "|".join((record.platform_id or "", record.domain, record.source_url, normalized, source_context)).encode("utf-8")
        ).hexdigest()
        with self.transaction() as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO comments(record_id,platform_id,normalized_text,text,domain,source_url,comment_url,publication_date,discovered_at,extraction_method,language_confidence,source_context)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, record.platform_id, normalized, record.text, record.domain, record.source_url, record.comment_url,
                 record.publication_date, record.discovered_at, record.extraction_method, record.language_confidence, source_context),
            )
            if cur.rowcount:
                db.execute("UPDATE domains SET comment_count=comment_count+1 WHERE domain=?", (record.domain,))
            return bool(cur.rowcount)

    def record_attempt(self, url: str, method: str, comments_found: int, success: bool, error: str | None = None) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO extraction_attempts(url,attempted_at,method,comments_found,success,error) VALUES(?,?,?,?,?,?)", (url, utc_now(), method, comments_found, int(success), error))

    def log(self, level: str, component: str, message: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO error_logs(created_at,level,component,message) VALUES(?,?,?,?)", (utc_now(), level, component, message[:1000]))
        log_path = self.logs_dir / "crawler.log"
        self.budget.ensure_capacity(len(message) + 100)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now()} [{level}] {component}: {message[:1000]}\n")

    def checkpoint(self, state: CrawlState, reason: str | None = None) -> Path:
        payload = {"session_id": self.session_id, "state": state.value, "reason": reason, "created_at": utc_now()}
        path = self.checkpoints_dir / "latest.json"
        self.budget.ensure_capacity(1024)
        fd, temp_name = tempfile.mkstemp(prefix="checkpoint-", suffix=".tmp", dir=self.temp_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        if self.session_id is not None:
            with self.transaction() as db:
                db.execute("UPDATE crawl_sessions SET checkpoint_path=? WHERE id=?", (str(path), self.session_id))
                db.execute("UPDATE storage_accounting SET last_measured_at=? WHERE session_id=?", (utc_now(), self.session_id))
        return path

    def count(self, table: str) -> int:
        allowed = {"domains", "discovered_urls", "comments", "extraction_attempts", "error_logs"}
        if table not in allowed:
            raise ValueError("unsupported count table")
        with self._lock:
            return int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def dashboard_counts(self) -> dict[str, int]:
        """Return persisted counters so a reopened GUI reflects the database."""
        with self._lock:
            return {
                "discovered_websites": int(self._connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0]),
                "visited_pages": int(self._connection.execute("SELECT COUNT(*) FROM discovered_urls WHERE visited_at IS NOT NULL").fetchone()[0]),
                "unique_comments": int(self._connection.execute("SELECT COUNT(*) FROM comments").fetchone()[0]),
                "errors": int(self._connection.execute("SELECT COUNT(*) FROM discovered_urls WHERE status='error'").fetchone()[0]),
                "storage_bytes": self.budget.usage(),
                "remaining_storage_bytes": self.budget.remaining(),
            }

    def comments_for_domain(self, domain: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if domain:
                return list(self._connection.execute("SELECT * FROM comments WHERE domain=? ORDER BY discovered_at", (domain,)))
            return list(self._connection.execute("SELECT * FROM comments ORDER BY discovered_at"))

    def was_exported(self, record_id: str) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM exported_records WHERE record_id=?", (record_id,)).fetchone() is not None
