# User manual

1. Launch `python run_app.py` or the packaged `PersianCommentCrawler.exe`.
2. Keep Safe Test Mode enabled for the first run. It stops after ten minutes, 5,000 comments, or the 1 GB managed session allowance.
3. To run without seed websites, keep **Automatically discover .ir websites** enabled and provide an approved Bing Web Search API key. The crawler runs built-in Persian comment queries, filters results to `.ir`, and discovers same-domain links, sitemaps, RSS, and Atom feeds from the returned pages. Manual public seed URLs remain supported.
4. Select a managed data directory. The app writes only inside that directory.
5. Use Start, Pause, Resume, Stop, or Emergency stop. Stop creates a checkpoint and never deletes committed records.
6. Export manually when ready. TXT files are grouped by sanitized domain under `Data/Comments/<domain>/` and use UTF-8.

The crawler is designed for public, authorized research. Do not add private URLs, authenticated pages, or sites that explicitly disallow crawling. A missing or unavailable robots file is treated as a bounded, logged request condition; it is not a reason to increase concurrency or bypass access controls.
