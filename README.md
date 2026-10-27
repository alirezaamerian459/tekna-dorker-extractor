# Persian Comment Crawler

Persian Comment Crawler is a Windows desktop application for collecting publicly accessible Persian-language comments from websites you are allowed to research. It discovers links from explicit seed URLs, same-domain pages, XML sitemaps, RSS, and Atom feeds; detects common comment structures; normalizes and classifies Persian text; stores provenance in SQLite; and exports optional UTF-8 TXT files grouped by domain.

The application starts in Safe Test Mode:

| Guardrail | Default |
| --- | ---: |
| Test duration | 10 minutes |
| Session storage allowance | 1 GB |
| Minimum free disk space | 10 GB |
| Maximum comments | 5,000 |
| Concurrent requests | 8 |
| Links queued per page | 30 |
| Low-yield domain cutoff | Disabled |
| TXT export | Manual |
| Recovery checkpoint | Enabled |

## Run on Windows

Python 3.12 or newer is required. Tkinter is included with the official Windows Python installer.

```powershell
python run_app.py
```

For a local editable install:

```powershell
python -m pip install -e .
persian-comment-crawler
```

To build a Windows executable after installing PyInstaller:

```powershell
.\build_windows.ps1
```

## What it does

- Includes automatic `.ir` discovery with a high-yield path: it ranks all discovered `.ir` domains using Tranco's daily public ranking, gives the top 50 a first-tier boost, then keeps every other discovered site in the normal queue. Archival Common Crawl URLs and optional authorized Bing Web Search results are still included. Manual seeds remain optional.
- Seeds a first-priority lane for CafeBazaar, Myket, Digikala, Torob, Soft98, and VGDL; comment/review URLs, app/product pages, and Digikala's public sitemap are visited before the ordinary `.ir` backlog, while navigation and downloads stay behind them.
- Does not impose a per-domain page cap or no-comments cutoff by default; overall session duration, storage, queue size, response size, robots rules, comment cap, and asset filters still prevent unbounded resource use.
- Expands sitemap indexes and child sitemaps without the ordinary 30-link page cap, and keeps every detected app/product/game URL in the queue; large URL batches are committed transactionally for crash-resistant discovery.
- Uses dynamic per-domain worker lanes: one active URL per host, many different hosts in parallel, and adaptive host pacing. A clean host converges from the two-second starting delay toward 0.5 seconds; timeouts, connection failures, 403/408/425/429 responses, and 5xx responses back off that host exponentially up to eight seconds. There is no global cross-site wait by default, so throughput scales with the number of available websites instead of alphabet buckets.
- The dashboard exposes live worker lanes, queued URLs, active/failed counts, focused-site progress, storage, and recent activity so a multi-domain run can be watched without guessing from a single counter.
- Checks robots.txt, uses a descriptive user agent, applies domain cooldowns, and rejects non-HTTP(S), embedded-credential, private, loopback, link-local, reserved, and multicast targets.
- Detects semantic comment structures, WordPress-style classes, reviews, replies, and Schema.org JSON-LD comments.
- Rejects common comment-widget noise such as reply controls, dates, counters, and "first comment" placeholders before saving.
- Prioritizes article/news links, skips known assets and low-value navigation, caps link expansion per page, and stops expanding domains that produce no valid comments.
- Normalizes Persian/Arabic Unicode variants while preserving the original comment text.
- Deduplicates comments with platform IDs when available and provenance-aware hashes otherwise.
- Keeps Start, Pause, Resume, Stop, and Emergency stop available from the desktop UI.
- Saves SQLite data, logs, temporary files, and checkpoints beneath one managed data directory.
- Enforces storage and free-disk limits before network work and export writes.

## Test

```powershell
python -m unittest discover -s tests -v
```

The tests use local fixtures and a local HTTP server. They do not crawl uncontrolled third-party websites.

See [docs/USER_MANUAL.md](docs/USER_MANUAL.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/DEVELOPER.md](docs/DEVELOPER.md) for operation and extension notes.

The original browser-only dorker interface remains in `index.html`, `styles.css`, and `app.js` for compatibility, but the desktop crawler is launched with `run_app.py`.

## Automatic `.ir` discovery

Enable **Automatically discover .ir websites** in the desktop app. The app reads Tranco's daily public ranking, gives the top 50 `.ir` domains a first-tier boost, and applies descending popularity tiers to every other discovered `.ir` URL. Nothing outside the top 50 is dropped; unknown sites keep their normal path/source priority. It also queues public review/catalog entry points for CafeBazaar, Myket, Digikala, Torob, Soft98, and VGDL, including Digikala's public sitemap path. It then adds URLs from the free Common Crawl URL Index without an account or API key. Common Crawl is archival and Tranco is a popularity ranking, so coverage and freshness vary. An optional Bing Web Search API key can add live results; it may also be supplied as `BING_SEARCH_API_KEY`. The app calls the documented JSON API, never scrapes a search-results page, and redacts the key from session configuration files.
