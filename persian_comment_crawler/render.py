from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderResult:
    html: str
    method: str
    error: str | None = None


class OptionalPlaywrightRenderer:
    """Render only an explicitly selected page when Playwright is installed.

    The default crawler does not launch a browser. This adapter exists for a
    future selective-rendering policy and intentionally has no login, upload,
    CAPTCHA, proxy, or file-download support.
    """

    def render(self, url: str, timeout_ms: int = 20_000) -> RenderResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return RenderResult("", "playwright-unavailable", "Install the optional render dependency first")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(java_script_enabled=True, accept_downloads=False)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                html = page.content()
                browser.close()
                return RenderResult(html, "playwright")
        except Exception as exc:
            return RenderResult("", "playwright-error", str(exc)[:300])
