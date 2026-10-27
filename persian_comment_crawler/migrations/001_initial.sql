-- Initial schema reference for migrating the SQLite store to PostgreSQL.
-- The runtime applies the same schema in storage.py so a first install is zero-config.
-- Keep table and index names stable when adding future migrations.
CREATE TABLE IF NOT EXISTS domains (
  domain TEXT PRIMARY KEY,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  discovered_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discovered_urls (
  url TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  discovery_source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 50,
  attempts INTEGER NOT NULL DEFAULT 0,
  discovered_at TEXT NOT NULL,
  visited_at TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS comments (
  record_id TEXT PRIMARY KEY,
  platform_id TEXT,
  normalized_text TEXT NOT NULL,
  text TEXT NOT NULL,
  domain TEXT NOT NULL,
  source_url TEXT NOT NULL,
  comment_url TEXT,
  publication_date TEXT,
  discovered_at TEXT NOT NULL,
  extraction_method TEXT NOT NULL,
  language_confidence REAL NOT NULL,
  source_context TEXT
);

CREATE TABLE IF NOT EXISTS exported_records (
  record_id TEXT PRIMARY KEY,
  exported_at TEXT NOT NULL,
  output_path TEXT NOT NULL
);
