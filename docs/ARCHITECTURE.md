# Architecture

The application is split into small modules so the crawler can be exercised without the GUI:

```text
Tkinter GUI -> CrawlEngine -> UrlFetcher -> HTML extractor
                    |              |             |
                    v              v             v
                 SQLiteStore <- discovery <- Persian classifier
                    |
                    +-> domain TXT exporter
```

## Safety boundaries

- `policy.py` accepts only HTTP(S), strips fragments, rejects embedded credentials, and rejects targets that resolve to private, loopback, link-local, reserved, or multicast IP addresses.
- `engine.py` checks `robots.txt` before every page, uses a descriptive user agent, applies domain cooldowns, bounds response size and queue size, and never rotates identities or proxies.
- `storage.py` owns the managed data root, checks both the session allowance and minimum free disk space, and writes checkpoints atomically.
- `exporter.py` sanitizes domain directory names and rotates UTF-8 output without overwriting an existing export.
- `config.py` bounds per-page link expansion and per-domain crawl budgets so internal-link discovery cannot grow without limit.

## Persistence

SQLite uses WAL mode and explicit transactions. The `comments.record_id` hash is stable for a platform ID when available, and otherwise combines domain, source URL, normalized text, and context. The schema is intentionally relational so it can migrate to PostgreSQL later by replacing the connection layer and retaining the table contracts.

## Discovery extension points

`discovery.py` handles manual seeds, internal links, XML sitemaps, RSS, Atom-style feeds, Tranco's public daily popularity ranking, a no-key Common Crawl URL Index provider, and an optional Bing JSON API provider. The built-in `.ir` mode filters every provider result to the `.ir` ccTLD. Tranco ranks all discovered `.ir` domains: the top 50 share a first-tier boost, later ranks receive descending tiers, and unknown domains retain their normal source/path priority. Existing pending URLs are re-ranked at session start, so the daily list affects both new and already queued work. Common Crawl provides archival coverage, and the optional live provider adds fresher results. Other search APIs, public directories, and dataset imports should implement the same provider contract; credentials and provider-specific terms stay outside the core crawler.

`render.py` is an optional Playwright adapter. It is not used automatically and has no authentication, CAPTCHA, download, or proxy behavior.

Comment extraction applies a noise filter before persistence. Structured `Comment` data and explicit comment-body fields are preferred; common controls, dates, reply counters, and empty-comment prompts are rejected. Internal links receive a path-aware priority, and domains with no valid comments after the low-yield threshold have their queued expansion skipped.
