from __future__ import annotations

import gzip
import io
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import CrawlConfig
from .policy import validate_public_url


def certificate_context() -> ssl.SSLContext:
    """Use certifi's maintained CA bundle when available on Windows."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    error: str | None = None
    elapsed_seconds: float = 0.0


class UrlFetcher:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config

    def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        try:
            safe_url = validate_public_url(url)
            request = urllib.request.Request(safe_url, headers={"User-Agent": self.config.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8"})
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds, context=certificate_context()) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type and not any(part in content_type for part in ("text/", "xml", "json", "javascript")):
                    return FetchResult(safe_url, int(response.status), content_type, b"", "non-text content skipped", time.monotonic() - started)
                body = response.read(self.config.max_response_bytes + 1)
                if len(body) > self.config.max_response_bytes:
                    return FetchResult(safe_url, int(response.status), content_type, b"", "response size limit reached", time.monotonic() - started)
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(self.config.max_response_bytes)
                return FetchResult(safe_url, int(response.status), content_type, body, None, time.monotonic() - started)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            return FetchResult(url, 0, "", b"", str(exc)[:300], time.monotonic() - started)

    @staticmethod
    def decode(result: FetchResult) -> str:
        if not result.body:
            return ""
        for encoding in ("utf-8", "utf-16", "cp1256", "iso-8859-6"):
            try:
                return result.body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return result.body.decode("utf-8", errors="replace")
