from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from html.parser import HTMLParser
import json
import os
import urllib.parse
import urllib.request
from urllib.parse import urljoin, urlsplit


NON_HTML_EXTENSIONS = (".7z", ".aab", ".apk", ".avi", ".bin", ".csv", ".dmg", ".doc", ".docx", ".exe", ".gif", ".gz", ".iso", ".jpeg", ".jpg", ".m4a", ".mkv", ".mov", ".msi", ".mp3", ".mp4", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar", ".webm", ".webp", ".xls", ".xlsx", ".xml.zip", ".zip")
LOW_VALUE_PATH_PARTS = {"amp", "archive", "author", "category", "feed", "login", "page", "register", "search", "tag", "tags", "wp-admin", "wp-json"}
HIGH_VALUE_PATH_PARTS = {"article", "articles", "blog", "news", "post", "posts", "review", "reviews", "single", "single-tracks"}
COMMENT_PATH_PARTS = {"comment", "comments", "discussion", "discussions", "feedback", "opinion", "opinions", "review", "reviews", "نظر", "نظرات", "دیدگاه", "دیدگاهها"}
APP_STORE_DOMAINS = {"cafebazaar.ir", "myket.ir"}
FOCUSED_DOMAINS = APP_STORE_DOMAINS | {"digikala.com", "torob.com", "soft98.ir", "vgdl.ir"}
FOCUSED_ENTRYPOINTS = (
    ("https://www.digikala.com/sitemap.xml", "focused-marketplace-sitemap", 1),
    ("https://cafebazaar.ir/app", "focused-app-store", 2),
    ("https://myket.ir/apps", "focused-app-store", 2),
    ("https://myket.ir/games", "focused-app-store", 2),
    ("https://www.digikala.com/", "focused-marketplace", 3),
    ("https://torob.com/", "focused-marketplace", 3),
    ("https://soft98.ir/", "focused-software", 3),
    ("https://vgdl.ir/", "focused-games", 3),
)


class FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag.lower() == "link" and values.get("href") and values.get("type", "").lower() in {"application/rss+xml", "application/atom+xml", "application/xml"}:
            self.links.append(values["href"])


def same_domain(url: str, domain: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    domain = domain.lower().removeprefix("www.")
    return hostname == domain or hostname.endswith(f".{domain}")


def link_priority(url: str, default: int = 50) -> int | None:
    """Return a crawl priority, or None for a known non-HTML asset."""
    parts = urlsplit(url)
    path = parts.path.lower()
    hostname = (parts.hostname or "").lower().removeprefix("www.")
    sitemap_gzip = path.endswith(".gz") and ("sitemap" in path or "site-map" in path)
    if (path.endswith(NON_HTML_EXTENSIONS) and not sitemap_gzip) or parts.query.lower().startswith(("replytocom=", "amp=1")):
        return None
    if sitemap_gzip:
        # Digikala publishes product sitemaps on a static subdomain. Keep
        # those index parts ahead of ordinary navigation so product URLs can
        # enter the queue before the domain budget is consumed.
        return min(default, 7 if hostname.endswith("digikala.com") else 18)
    decoded_path = urllib.parse.unquote(path)
    segments = {segment for segment in decoded_path.split("/") if segment}
    query_text = parts.query.lower()
    if segments & COMMENT_PATH_PARTS or any(hint in query_text for hint in ("comment", "review", "feedback", "discussion")):
        return min(default, 10)
    if hostname in APP_STORE_DOMAINS:
        if path.startswith("/app/"):
            return min(default, 5)
        if path in {"/app", "/apps", "/games"} or path.startswith(("/apps/", "/games/")):
            return min(default, 10)
    if hostname == "digikala.com" and path.startswith("/product/"):
        return min(default, 5)
    if hostname == "torob.com" and path.startswith("/p/"):
        return min(default, 5)
    if hostname == "digikala.com" and path == "/sitemap.xml":
        return min(default, 7)
    if hostname in FOCUSED_DOMAINS:
        return min(default, 15)
    if segments & LOW_VALUE_PATH_PARTS:
        return 80
    if segments & HIGH_VALUE_PATH_PARTS:
        return 30
    return default


def extract_html_links(html: str, base_url: str, domain: str | None = None) -> list[str]:
    parser = FeedLinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    links = [urljoin(base_url, link) for link in parser.links]
    return list(dict.fromkeys(link for link in links if urlsplit(link).scheme in {"http", "https"} and link_priority(link) is not None and (not domain or same_domain(link, domain))))


def extract_sitemap_urls(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    result: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text:
            value = element.text.strip()
            if value.startswith(("http://", "https://")):
                result.append(value)
    return list(dict.fromkeys(result))


def extract_feed_urls(xml_text: str) -> list[str]:
    return extract_sitemap_urls(xml_text)


def discovery_candidates(page_url: str, response_text: str, content_type: str = "") -> Iterable[tuple[str, str, int]]:
    content_type = content_type.lower()
    if "xml" in content_type or page_url.lower().endswith((".xml", "sitemap")):
        for url in extract_sitemap_urls(response_text):
            priority = link_priority(url, 20)
            if priority is not None:
                yield url, "sitemap-or-feed", priority
    domain = (urlsplit(page_url).hostname or "").lower()
    for url in extract_html_links(response_text, page_url, domain):
        priority = link_priority(url)
        if priority is not None:
            yield url, "internal-link", priority


class SearchProvider:
    """Optional provider contract for permitted search APIs."""

    name = "none"

    def search(self, query: str, limit: int = 20) -> list[str]:
        return []


class CommonCrawlProvider:
    """Discover archived URLs from Common Crawl without an account or key."""

    index_catalog_url = "https://index.commoncrawl.org/collinfo.json"

    def __init__(self, user_agent: str = "PersianCommentCrawler/0.1", timeout: int = 20) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.last_error: str | None = None

    def latest_index(self) -> str | None:
        request = urllib.request.Request(self.index_catalog_url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                values = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception as exc:
            self.last_error = str(exc)[:200]
            return None
        if not isinstance(values, list):
            self.last_error = "Common Crawl returned an unexpected index catalog"
            return None
        for item in values:
            if isinstance(item, dict) and (item.get("id") or item.get("name")):
                return str(item.get("id") or item.get("name"))
        self.last_error = "Common Crawl returned no crawl indexes"
        return None

    def discover_ir(self, limit: int = 100) -> list[str]:
        index_name = self.latest_index()
        if not index_name:
            return []
        params = urllib.parse.urlencode({
            # The broad wildcard form can time out at the public index proxy.
            # Domain matching for the ccTLD is faster and still returns page URLs.
            "url": "ir",
            "matchType": "domain",
            "output": "json",
            "filter": "status:200",
            "collapse": "urlkey",
            "limit": max(1, min(limit, 500)),
        })
        endpoint = f"https://index.commoncrawl.org/{urllib.parse.quote(index_name)}-index?{params}"
        request = urllib.request.Request(endpoint, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4_000_000).decode("utf-8", errors="replace")
        except Exception as exc:
            self.last_error = str(exc)[:200]
            return []
        urls: list[str] = []
        for line in raw.splitlines():
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            value = record.get("url", "") if isinstance(record, dict) else ""
            if value.startswith(("http://", "https://")) and (urllib.parse.urlsplit(value).hostname or "").lower().endswith(".ir"):
                urls.append(value)
        self.last_error = None
        return list(dict.fromkeys(urls))


class TrancoProvider:
    """Find popular `.ir` domains from Tranco's public daily ranking."""

    metadata_url = "https://tranco-list.eu/api/lists/date/latest"

    def __init__(self, user_agent: str = "PersianCommentCrawler/0.1", timeout: int = 30) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.last_error: str | None = None
        self._ir_rankings: dict[str, int] | None = None

    @staticmethod
    def _canonical_domain(domain: str) -> str:
        domain = domain.strip().lower().rstrip(".")
        return domain[4:] if domain.startswith("www.") else domain

    def load_ir_rankings(self) -> dict[str, int]:
        """Load ranks for every `.ir` domain in the public daily list once."""
        if self._ir_rankings is not None:
            return self._ir_rankings
        try:
            metadata_request = urllib.request.Request(self.metadata_url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
            with urllib.request.urlopen(metadata_request, timeout=self.timeout) as response:
                metadata = json.loads(response.read(100_000).decode("utf-8", errors="replace"))
            download_url = metadata.get("download") if isinstance(metadata, dict) else None
            if not download_url:
                self.last_error = "Tranco returned no download URL"
                self._ir_rankings = {}
                return self._ir_rankings
            request = urllib.request.Request(download_url, headers={"User-Agent": self.user_agent, "Accept": "text/csv"})
            rankings: dict[str, int] = {}
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    try:
                        line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line)
                        rank_text, domain = line.strip().split(",", 1)
                        rank = int(rank_text)
                    except ValueError:
                        continue
                    domain = self._canonical_domain(domain)
                    if domain.endswith(".ir"):
                        rankings.setdefault(domain, rank)
            self._ir_rankings = rankings
            self.last_error = None
            return rankings
        except Exception as exc:
            self.last_error = str(exc)[:200]
            self._ir_rankings = {}
            return self._ir_rankings

    def discover_ir(self, limit: int = 50) -> list[str]:
        rankings = self.load_ir_rankings()
        return [f"https://{domain}/" for domain, _rank in sorted(rankings.items(), key=lambda item: item[1])[: max(1, min(limit, 200))]]

    def domain_rank(self, url: str) -> int | None:
        hostname = self._canonical_domain(urlsplit(url).hostname or "")
        rankings = self.load_ir_rankings()
        parts = hostname.split(".")
        for index in range(max(0, len(parts) - 1)):
            candidate = ".".join(parts[index:])
            if candidate in rankings:
                return rankings[candidate]
        return rankings.get(hostname)

    def priority_for_url(self, url: str, fallback: int = 35) -> int:
        """Boost every ranked domain while preserving the fallback ordering."""
        rank = self.domain_rank(url)
        if rank is None:
            return fallback
        # Top 50 share the popular-root tier; later ranks are still preferred
        # over unknown domains without removing anything from the queue.
        if rank <= 50:
            return min(fallback, 15)
        return min(fallback, 16 + ((rank - 51) // 100))


class BingSearchProvider(SearchProvider):
    """Use Bing Web Search only when the user supplies an API key.

    The crawler never scrapes a search-results page. This provider calls the
    documented JSON endpoint and remains disabled when the key is absent.
    """

    name = "bing-api"

    def __init__(self, api_key: str | None = None, endpoint: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("BING_SEARCH_API_KEY", "")
        self.endpoint = endpoint or os.environ.get("BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, limit: int = 20) -> list[str]:
        if not self.enabled:
            return []
        params = urllib.parse.urlencode({"q": query, "count": max(1, min(limit, 50)), "responseFilter": "Webpages"})
        request = urllib.request.Request(f"{self.endpoint}?{params}", headers={"Ocp-Apim-Subscription-Key": self.api_key, "Accept": "application/json", "User-Agent": "PersianCommentCrawler/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8", errors="replace"))
        except Exception:
            return []
        values = data.get("webPages", {}).get("value", []) if isinstance(data, dict) else []
        return list(dict.fromkeys(item.get("url", "") for item in values if isinstance(item, dict) and item.get("url", "").startswith(("http://", "https://"))))

    def search_ir(self, query: str, limit: int = 20) -> list[str]:
        """Search only the Iranian ccTLD without scraping result pages."""
        values = self.search(f"site:.ir {query}", limit)
        return [url for url in values if (urllib.parse.urlsplit(url).hostname or "").lower().endswith(".ir")]


def default_search_queries() -> tuple[str, ...]:
    return (
        '"نظرات کاربران"',
        '"دیدگاه کاربران"',
        '"پاسخ به دیدگاه"',
        '"ثبت دیدگاه"',
        '"Comments" "Reviews" Persian',
    )
