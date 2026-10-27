from __future__ import annotations

import queue
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from .config import CrawlConfig
from .discovery import FOCUSED_ENTRYPOINTS, BingSearchProvider, CommonCrawlProvider, TrancoProvider, default_search_queries, discovery_candidates, link_priority
from .extractor import extract_page
from .fetcher import UrlFetcher
from .models import CommentRecord, CrawlState, CrawlStats, utc_now
from .policy import UnsafeUrl, normalize_url
from .storage import SQLiteStore, StorageBudgetExceeded


NON_CONTENT_HOST_PREFIXES = ("ads", "assets", "cdn", "dl", "download", "img", "images", "media", "static", "video", "vod")


class CrawlEngine:
    """A persistent, bounded crawler with explicit pause/stop transitions."""

    def __init__(self, config: CrawlConfig, store: SQLiteStore, on_event=None) -> None:
        self.config = config
        self.store = store
        self.on_event = on_event or (lambda event, payload: None)
        self.fetcher = UrlFetcher(config)
        self.search_provider = BingSearchProvider(config.search_api_key or None)
        self.common_crawl_provider = CommonCrawlProvider(config.user_agent, min(config.request_timeout_seconds, 12))
        self.tranco_provider = TrancoProvider(config.user_agent, min(config.request_timeout_seconds, 30))
        self.state = CrawlState.IDLE
        persisted = store.dashboard_counts()
        self.stats = CrawlStats(
            discovered_websites=persisted["discovered_websites"],
            visited_pages=persisted["visited_pages"],
            processed_pages=persisted["visited_pages"],
            unique_comments=persisted["unique_comments"],
            errors=persisted["errors"],
            storage_bytes=persisted["storage_bytes"],
            remaining_storage_bytes=persisted["remaining_storage_bytes"],
        )
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._emergency_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._robot_cache: dict[str, RobotFileParser | None] = {}
        self._last_domain_request: dict[str, float] = defaultdict(float)
        self._domain_pages: dict[str, int] = defaultdict(int)
        self._domain_comment_pages: dict[str, int] = defaultdict(int)
        self._domain_stats_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._started_at = 0.0

    def emit(self, event: str, payload=None) -> None:
        self.on_event(event, payload)

    def start(self) -> None:
        with self._state_lock:
            if self.state in {CrawlState.RUNNING, CrawlState.PAUSED, CrawlState.STOPPING}:
                return
            self._stop_event.clear()
            self._emergency_event.clear()
            self._pause_event.clear()
            self.store.budget.ensure_capacity(0)
            self.store.start_session(self.config)
            requeued = self.store.requeue_incomplete_urls(self.config.max_retries)
            pruned_assets = self.store.skip_pending_host_prefixes(NON_CONTENT_HOST_PREFIXES, "asset/CDN host excluded")
            pruned_budget = self.store.skip_pending_over_budget_domains(self.config.max_pages_per_domain, "domain page budget already exhausted")
            self._started_at = time.monotonic()
            self.state = CrawlState.RUNNING
            self.store.set_session_state(self.state)
            self._thread = threading.Thread(target=self._run, name="crawler-dispatcher", daemon=True)
            self._thread.start()
            self.emit("state", self.state)
            self.emit("stats", asdict(self.stats))
            if requeued:
                self.emit("activity", f"Recovered {requeued} interrupted URL(s)")
            if pruned_assets or pruned_budget:
                self.emit("activity", f"Pruned {pruned_assets + pruned_budget} low-value queued URL(s)")

    def pause(self) -> None:
        with self._state_lock:
            if self.state != CrawlState.RUNNING:
                return
            self._pause_event.set()
            self.state = CrawlState.PAUSED
            self.store.set_session_state(self.state)
            self.store.checkpoint(self.state, "user pause")
            self.emit("state", self.state)

    def resume(self) -> None:
        with self._state_lock:
            if self.state not in {CrawlState.PAUSED, CrawlState.BUDGET_PAUSED, CrawlState.STOPPED}:
                return
            self._pause_event.clear()
            self._stop_event.clear()
            self.state = CrawlState.RUNNING
            self.store.set_session_state(self.state)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="crawler-dispatcher", daemon=True)
                self._thread.start()
            self.emit("state", self.state)

    def stop(self) -> None:
        with self._state_lock:
            if self.state not in {CrawlState.RUNNING, CrawlState.PAUSED, CrawlState.BUDGET_PAUSED}:
                return
            self.state = CrawlState.STOPPING
            self._stop_event.set()
            self._pause_event.clear()
            self.store.set_session_state(self.state)
            self.emit("state", self.state)

    def emergency_stop(self) -> None:
        with self._state_lock:
            self._emergency_event.set()
            self._stop_event.set()
            self._pause_event.clear()
            self.state = CrawlState.STOPPING
            self.store.set_session_state(self.state, "emergency stop")
            self.emit("state", self.state)

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _seed_urls(self) -> None:
        before = self.store.count("discovered_urls")
        if self.config.auto_ir_discovery:
            # Apply today's ranking to an existing backlog as well as new URLs.
            self.tranco_provider.load_ir_rankings()
            reprioritized = self.store.reprioritize_pending(self._priority_for_url)
            if reprioritized:
                self.emit("activity", f"Applied popularity tiers to {reprioritized} queued URL(s)")
            self._seed_focused_targets()
        if self.store.has_pending_urls():
            # Keep a small high-yield refresh on every session so an older
            # backlog cannot starve newly ranked popular domains.
            if self.config.auto_ir_discovery:
                self._seed_popular_ir(refresh=True)
            return
        if self.config.auto_ir_discovery and self.search_provider.enabled:
            self.emit("activity", "Discovering Iranian .ir pages through the configured search API")
            for query in default_search_queries():
                for url in self.search_provider.search_ir(query, limit=self.config.search_results_per_query):
                    try:
                        safe = normalize_url(url)
                    except UnsafeUrl:
                        continue
                    self.store.upsert_url(safe, f"{self.search_provider.name}: {query}", self._priority_for_url(safe, 25))
        if self.config.auto_ir_discovery:
            self._seed_popular_ir()
            self.emit("activity", "Discovering archived .ir pages through Common Crawl (up to 12 seconds)")
            common_urls = self.common_crawl_provider.discover_ir(limit=max(25, self.config.search_results_per_query * 3))
            for url in common_urls:
                try:
                    safe = normalize_url(url)
                except UnsafeUrl:
                    continue
                self.store.upsert_url(safe, "common-crawl-index", self._priority_for_url(safe, 35))
            if self.common_crawl_provider.last_error:
                self.emit("activity", f"Common Crawl unavailable: {self.common_crawl_provider.last_error}")
            else:
                self.emit("activity", f"Common Crawl returned {len(common_urls)} .ir URLs")
        if not self.config.seed_urls and not self.store.has_pending_urls():
            if self.config.auto_ir_discovery and not self.search_provider.enabled:
                self.emit("activity", "No live search API key configured; Tranco and Common Crawl discovery are still available")
            else:
                self.emit("activity", "No discovery results were returned")
        queued = self.store.count("discovered_urls") - before
        self.emit("activity", f"Discovery complete — {queued} new URL(s) queued")
        for raw in self.config.seed_urls:
            try:
                url = normalize_url(raw)
            except UnsafeUrl as exc:
                self.store.log("warning", "discovery", f"Skipped seed {raw!r}: {exc}")
                continue
            if self.store.upsert_url(url, "manual-seed", 10):
                self.emit("activity", f"Queued seed: {url}")

    def _seed_popular_ir(self, refresh: bool = False) -> None:
        self.emit(
            "activity",
            "Refreshing popular .ir domains from the daily Tranco ranking"
            if refresh
            else "Prioritizing popular .ir domains from the daily Tranco ranking",
        )
        popular_urls = self.tranco_provider.discover_ir(limit=50)
        for url in popular_urls:
            try:
                safe = normalize_url(url)
            except UnsafeUrl:
                continue
            self.store.upsert_url(safe, "tranco-popular-ir", 15)
        if self.tranco_provider.last_error:
            self.emit("activity", f"Tranco unavailable: {self.tranco_provider.last_error}")
        else:
            self.emit("activity", f"Tranco returned {len(popular_urls)} popular .ir domains")

    def _seed_focused_targets(self) -> None:
        for raw_url, source, priority in FOCUSED_ENTRYPOINTS:
            try:
                url = normalize_url(raw_url)
            except UnsafeUrl:
                continue
            focused_priority = self._priority_for_url(url, priority)
            if self.store.upsert_url(url, source, focused_priority):
                self.emit("activity", f"Queued focused target: {url}")

    def _priority_for_url(self, url: str, fallback: int) -> int:
        # Re-score the URL shape first so an old priority-50 backlog cannot
        # bury comment, app, product, or sitemap pages. Popularity then acts
        # as a tie-breaker for ordinary pages and unknown domains.
        structural = link_priority(url, fallback)
        if structural is None:
            return fallback
        if not self.config.auto_ir_discovery:
            return structural
        return self.tranco_provider.priority_for_url(url, structural)

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robot_cache:
            parser = self._robot_cache[origin]
            return parser.can_fetch(self.config.user_agent, url) if parser is not None else True
        parser: RobotFileParser | None = RobotFileParser(f"{origin}/robots.txt")
        try:
            request = urllib.request.Request(f"{origin}/robots.txt", headers={"User-Agent": self.config.user_agent})
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                parser.parse(response.read(512_000).decode("utf-8", errors="replace").splitlines())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            parser = None
        if parser is None:
            # A missing or unavailable robots file does not grant permission;
            # we allow only a bounded request and record the condition.
            self.emit("activity", f"robots.txt unavailable: {origin}")
        self._robot_cache[origin] = parser
        return parser.can_fetch(self.config.user_agent, url) if parser is not None else True

    def _wait_if_paused(self) -> bool:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.15)
        return not self._stop_event.is_set()

    def _run(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=self.config.concurrency, thread_name_prefix="crawler-worker")
        try:
            self.emit("activity", "Starting discovery…")
            self._seed_urls()
            futures = set()
            while not self._stop_event.is_set():
                if self.config.safe_test_mode and time.monotonic() - self._started_at >= self.config.test_duration_seconds:
                    self.emit("activity", "Safe Test Mode duration reached")
                    break
                if not self._wait_if_paused():
                    break
                while len(futures) < self.config.concurrency and not self._stop_event.is_set():
                    row = self.store.claim_url()
                    if row is None:
                        break
                    futures.add(self._executor.submit(self._process_url, row["url"]))
                done = {future for future in futures if future.done()}
                for future in done:
                    futures.remove(future)
                    try:
                        future.result()
                    except Exception as exc:
                        self.store.log("error", "engine", str(exc))
                        self.stats.errors += 1
                if not futures and not self.store.has_pending_urls():
                    break
                time.sleep(0.05)
            for future in list(futures):
                if self._emergency_event.is_set():
                    future.cancel()
                else:
                    try:
                        future.result(timeout=self.config.request_timeout_seconds + 2)
                    except Exception:
                        future.cancel()
        finally:
            if self._executor:
                self._executor.shutdown(wait=not self._emergency_event.is_set(), cancel_futures=True)
            with self._state_lock:
                if self.state == CrawlState.STOPPING:
                    self.state = CrawlState.STOPPED
                elif self.state == CrawlState.RUNNING:
                    self.state = CrawlState.COMPLETED
                self.store.set_session_state(self.state)
                self.store.checkpoint(self.state, "worker finished")
                self.emit("state", self.state)
                self.emit("stats", asdict(self.stats))

    def _process_url(self, url: str) -> None:
        if self._stop_event.is_set():
            return
        if urlsplit(url).path.lower().endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mp3")):
            self.store.complete_url(url, status="skipped")
            self.store.record_attempt(url, "asset-filter", 0, True, "non-HTML asset skipped")
            self.emit("activity", f"Skipped non-HTML asset: {url}")
            return
        domain = (urlsplit(url).hostname or "").lower()
        # The claimed URL is already counted as active, so allow exactly the
        # configured maximum and reject only the next page.
        if self.store.domain_page_count(domain) > self.config.max_pages_per_domain:
            self.store.complete_url(url, status="skipped")
            self.store.record_attempt(url, "domain-limit", 0, True, "domain page limit reached")
            self.emit("activity", f"Skipped domain page limit: {url}")
            return
        elapsed = time.monotonic() - self._last_domain_request[domain]
        if elapsed < self.config.domain_cooldown_seconds:
            time.sleep(self.config.domain_cooldown_seconds - elapsed)
        self._last_domain_request[domain] = time.monotonic()
        if not self._allowed_by_robots(url):
            self.store.complete_url(url, "disallowed by robots.txt")
            self.emit("activity", f"Skipped by robots.txt: {url}")
            return
        try:
            self.store.budget.ensure_capacity(self.config.max_response_bytes)
        except StorageBudgetExceeded as exc:
            with self._state_lock:
                self.state = CrawlState.BUDGET_PAUSED
                self._pause_event.set()
            self.store.set_session_state(self.state, str(exc))
            self.store.checkpoint(self.state, str(exc))
            self.emit("budget", str(exc))
            return
        self.stats.active_requests += 1
        result = self.fetcher.fetch(url)
        self.stats.active_requests = max(0, self.stats.active_requests - 1)
        self.stats.visited_pages += 1
        if result.error:
            if result.error == "non-text content skipped":
                self.store.complete_url(url, status="skipped")
                self.store.record_attempt(url, "http", 0, True, result.error)
                self.emit("activity", f"Skipped non-text content: {url}")
                return
            self.stats.errors += 1
            self.store.complete_url(url, result.error)
            self.store.record_attempt(url, "http", 0, False, result.error)
            self.emit("activity", f"Fetch failed: {url} ({result.error})")
            return
        text = self.fetcher.decode(result)
        page = extract_page(text, url)
        new_comments = 0
        if self.store.count("comments") < self.config.maximum_comments:
            for item in page.comments:
                if self.store.count("comments") >= self.config.maximum_comments:
                    break
                if self.store.save_comment(CommentRecord(item.text, url, domain, item.comment_url, item.publication_date, item.method, item.confidence, item.platform_id)):
                    new_comments += 1
        candidates = sorted(discovery_candidates(url, text, result.content_type), key=lambda item: item[2])
        for discovered, source, priority in candidates[: self.config.max_links_per_page]:
            if self.store.count("discovered_urls") >= self.config.max_discovered_urls:
                break
            try:
                safe = normalize_url(discovered)
            except UnsafeUrl:
                continue
            self.store.upsert_url(safe, source, self._priority_for_url(safe, priority))
        self.store.record_attempt(url, page.method, len(page.comments), True)
        self.store.complete_url(url)
        self.stats.processed_pages += 1
        self.stats.pages_with_comments += int(bool(page.comments))
        with self._domain_stats_lock:
            self._domain_pages[domain] += 1
            self._domain_comment_pages[domain] += int(bool(page.comments))
            domain_pages = self._domain_pages[domain]
            domain_comment_pages = self._domain_comment_pages[domain]
        if domain_pages >= self.config.max_pages_per_domain:
            skipped = self.store.skip_pending_domain(domain, "domain page limit reached")
            if skipped:
                self.emit("activity", f"Stopped expansion for {domain} after {domain_pages} pages")
        elif self.config.low_yield_domain_pages and domain_pages >= self.config.low_yield_domain_pages and domain_comment_pages == 0:
            skipped = self.store.skip_pending_domain(domain, "low comment yield domain")
            if skipped:
                self.emit("activity", f"Stopped low-yield domain {domain} after {domain_pages} pages")
        self.stats.discovered_websites = self.store.count("domains")
        self.stats.unique_comments = self.store.count("comments")
        self.stats.storage_bytes = self.store.budget.usage()
        self.stats.remaining_storage_bytes = self.store.budget.remaining()
        self.stats.last_activity = utc_now()
        self.emit("stats", asdict(self.stats))
        self.emit("activity", f"Processed {url} — {new_comments} new Persian comments")
        if self.stats.remaining_storage_bytes <= 0:
            with self._state_lock:
                self.state = CrawlState.BUDGET_PAUSED
                self._pause_event.set()
            reason = "managed storage allowance reached after a committed page"
            self.store.set_session_state(self.state, reason)
            self.store.checkpoint(self.state, reason)
            self.emit("budget", reason)
