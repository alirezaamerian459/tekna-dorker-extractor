from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_data_root() -> Path:
    """Return a user-writable Windows application-data location."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "Persian-Comment-Crawler"
    return Path.home() / "Persian-Comment-Crawler"


@dataclass(slots=True)
class CrawlConfig:
    """Runtime settings, intentionally conservative by default."""

    safe_test_mode: bool = True
    test_duration_seconds: int = 600
    storage_limit_bytes: int = 1_000_000_000
    minimum_free_disk_bytes: int = 10_000_000_000
    maximum_comments: int = 5_000
    # Workers are assigned distinct active domains by the scheduler.
    concurrency: int = 8
    request_timeout_seconds: int = 20
    # Pace requests to the same host and when switching between hosts.
    domain_cooldown_seconds: float = 2.0
    # Adaptive host pacing keeps clean hosts fast while backing off noisy ones.
    domain_min_cooldown_seconds: float = 0.5
    domain_max_cooldown_seconds: float = 8.0
    # Keep different domains parallel by default; set >0 only for a global
    # cross-domain throttle.
    inter_domain_cooldown_seconds: float = 0.0
    max_retries: int = 2
    max_response_bytes: int = 4_000_000
    # Zero disables the per-domain page cap. Overall session, storage, queue,
    # response-size, and comment-count safeguards still apply.
    max_pages_per_domain: int = 0
    # Zero disables the optional no-comments early-stop heuristic.
    low_yield_domain_pages: int = 0
    max_links_per_page: int = 30
    # Zero disables the global discovered-URL cap. Session time, storage,
    # robots, response-size, and comment safeguards still apply.
    max_discovered_urls: int = 0
    export_rotation_bytes: int = 50_000_000
    user_agent: str = "PersianCommentCrawler/0.1 (+public-research; contact: local-user)"
    auto_ir_discovery: bool = True
    search_api_key: str = ""
    search_results_per_query: int = 20
    seed_urls: list[str] = field(default_factory=list)
    data_root: Path = field(default_factory=default_data_root)

    def __post_init__(self) -> None:
        self.concurrency = max(1, min(int(self.concurrency), 32))
        self.max_retries = max(0, min(int(self.max_retries), 8))
        self.search_results_per_query = max(1, min(int(self.search_results_per_query), 50))
        self.maximum_comments = max(1, int(self.maximum_comments))
        self.max_pages_per_domain = max(0, min(int(self.max_pages_per_domain), 1_000_000))
        self.low_yield_domain_pages = max(0, min(int(self.low_yield_domain_pages), 1_000_000))
        if self.max_pages_per_domain:
            self.low_yield_domain_pages = min(self.low_yield_domain_pages, self.max_pages_per_domain)
        self.domain_min_cooldown_seconds = max(0.0, min(float(self.domain_min_cooldown_seconds), 300.0))
        self.domain_max_cooldown_seconds = max(self.domain_min_cooldown_seconds, min(float(self.domain_max_cooldown_seconds), 300.0))
        # A zero base cooldown remains a supported explicit opt-out for local
        # fixtures and trusted hosts; adaptive pacing can still back off later.
        self.domain_cooldown_seconds = max(0.0, min(float(self.domain_cooldown_seconds), self.domain_max_cooldown_seconds))
        self.inter_domain_cooldown_seconds = max(0.0, min(float(self.inter_domain_cooldown_seconds), 300.0))
        self.max_links_per_page = max(5, min(int(self.max_links_per_page), 200))
        self.max_discovered_urls = max(0, min(int(self.max_discovered_urls), 10_000_000))
        self.storage_limit_bytes = max(1_000_000, int(self.storage_limit_bytes))
        self.minimum_free_disk_bytes = max(0, int(self.minimum_free_disk_bytes))
        self.data_root = Path(self.data_root).expanduser()

    @property
    def session_limit_bytes(self) -> int:
        return self.storage_limit_bytes if self.safe_test_mode else max(self.storage_limit_bytes, 1_000_000_000)

    def to_json(self) -> str:
        data = asdict(self)
        data["data_root"] = str(self.data_root)
        # Never persist a search API secret in a session checkpoint/database.
        data["search_api_key"] = "[configured]" if self.search_api_key else ""
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "CrawlConfig":
        data: dict[str, Any] = json.loads(raw)
        if data.get("search_api_key") == "[configured]":
            data["search_api_key"] = ""
        return cls(**data)
