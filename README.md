# Persian Comment Crawler

Persian Comment Crawler is a Windows desktop application for collecting publicly accessible Persian-language comments from websites you are allowed to research. It discovers links from explicit seed URLs, same-domain pages, XML sitemaps, RSS, and Atom feeds; detects common comment structures; normalizes and classifies Persian text; stores provenance in SQLite; and exports optional UTF-8 TXT files grouped by domain.

The application starts in Safe Test Mode:

| Guardrail | Default |
| --- | ---: |
| Test duration | 10 minutes |
| Session storage allowance | 1 GB |
| Minimum free disk space | 10 GB |
| Maximum comments | 5,000 |
| Concurrent requests | 2 |
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

- Includes automatic `.ir` discovery with a high-yield path: it first prioritizes popular `.ir` domains from Tranco's daily public ranking, then adds archival Common Crawl URLs and optional authorized Bing Web Search results. Manual seeds remain optional.
- Checks robots.txt, uses a descriptive user agent, applies domain cooldowns, and rejects non-HTTP(S), embedded-credential, private, loopback, link-local, reserved, and multicast targets.
- Detects semantic comment structures, WordPress-style classes, reviews, replies, and Schema.org JSON-LD comments.
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

Enable **Automatically discover .ir websites** in the desktop app. The app first reads Tranco's daily public ranking and queues popular `.ir` domains at the highest priority, then adds URLs from the free Common Crawl URL Index without an account or API key. It filters results to the `.ir` country-code domain and crawls those public pages. Common Crawl is archival and Tranco is a popularity ranking, so coverage and freshness vary. An optional Bing Web Search API key can add live results; it may also be supplied as `BING_SEARCH_API_KEY`. The app calls the documented JSON API, never scrapes a search-results page, and redacts the key from session configuration files.
