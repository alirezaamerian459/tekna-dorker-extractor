from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CrawlState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    BUDGET_PAUSED = "budget_paused"
    ERROR = "error"


@dataclass(slots=True)
class CommentRecord:
    text: str
    source_url: str
    domain: str
    comment_url: str | None = None
    publication_date: str | None = None
    extraction_method: str = "html-heuristic"
    language_confidence: float = 0.0
    platform_id: str | None = None
    discovered_at: str = field(default_factory=utc_now)
    record_id: str | None = None


@dataclass(slots=True)
class CrawlStats:
    discovered_websites: int = 0
    visited_pages: int = 0
    processed_pages: int = 0
    pages_with_comments: int = 0
    unique_comments: int = 0
    pending_urls: int = 0
    active_urls: int = 0
    failed_urls: int = 0
    current_speed: float = 0.0
    active_requests: int = 0
    errors: int = 0
    storage_bytes: int = 0
    remaining_storage_bytes: int = 0
    last_activity: str = ""

