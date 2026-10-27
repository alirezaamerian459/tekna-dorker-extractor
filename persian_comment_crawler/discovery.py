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
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname == domain or hostname.endswith(f".{domain}")


def extract_html_links(html: str, base_url: str, domain: str | None = None) -> list[str]:
    parser = FeedLinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    links = [urljoin(base_url, link) for link in parser.links]
    return list(dict.fromkeys(link for link in links if urlsplit(link).scheme in {"http", "https"} and (not domain or same_domain(link, domain))))


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
            yield url, "sitemap-or-feed", 20
    domain = (urlsplit(page_url).hostname or "").lower()
    for url in extract_html_links(response_text, page_url, domain):
        yield url, "internal-link", 50


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

    def latest_index(self) -> str | None:
        request = urllib.request.Request(self.index_catalog_url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                values = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception:
            return None
        if not isinstance(values, list):
            return None
        for item in values:
            if isinstance(item, dict) and (item.get("id") or item.get("name")):
                return str(item.get("id") or item.get("name"))
        return None

    def discover_ir(self, limit: int = 100) -> list[str]:
        index_name = self.latest_index()
        if not index_name:
            return []
        params = urllib.parse.urlencode({
            "url": "*.ir/*",
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
        except Exception:
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
        return list(dict.fromkeys(urls))


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
