CREATE TABLE kv (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE collections (
  id                INTEGER PRIMARY KEY,
  slug              TEXT NOT NULL UNIQUE,
  owner_type        TEXT NOT NULL CHECK (owner_type IN ('group','user')),
  owner_id          INTEGER NOT NULL,          -- telegram chat id (group) or user id (user); 0 = local CLI
  name              TEXT NOT NULL,
  start_date        TEXT,                       -- ISO date YYYY-MM-DD
  end_date          TEXT,
  expires_at        TEXT,                       -- NULL = persistent
  tier              TEXT NOT NULL DEFAULT 'free',
  state             TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','expired','deleted')),
  version           INTEGER NOT NULL DEFAULT 0,
  published_version INTEGER NOT NULL DEFAULT -1,
  created_at        TEXT NOT NULL,
  expired_at        TEXT,
  unpublished_at    TEXT
);
CREATE INDEX ix_collections_state_expires ON collections(state, expires_at);
CREATE INDEX ix_collections_owner ON collections(owner_type, owner_id);

CREATE TABLE groups (
  telegram_chat_id     INTEGER PRIMARY KEY,
  active_collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  reply_mode           TEXT NOT NULL DEFAULT 'digest' CHECK (reply_mode IN ('quiet','ack','digest')),
  paused               INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
  timezone             TEXT NOT NULL DEFAULT 'Asia/Manila',
  tier                 TEXT NOT NULL DEFAULT 'free',
  added_at             TEXT NOT NULL,
  removed_at           TEXT
);

CREATE TABLE reels (
  id                INTEGER PRIMARY KEY,
  collection_id     INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  canonical_url     TEXT NOT NULL,
  platform          TEXT NOT NULL CHECK (platform IN ('instagram','tiktok','youtube')),
  status            TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','done','failed')),
  attempts          INTEGER NOT NULL DEFAULT 0,
  error_code        TEXT,
  error_detail      TEXT,
  author            TEXT,
  submitted_by      INTEGER,                    -- opaque telegram user id; never shown publicly
  source_message_id INTEGER,
  submitted_at      TEXT NOT NULL,
  claimed_at        TEXT,
  processed_at      TEXT,
  notified_at       TEXT,
  UNIQUE (collection_id, canonical_url)
);
CREATE INDEX ix_reels_status_submitted ON reels(status, submitted_at);
CREATE INDEX ix_reels_collection ON reels(collection_id);

CREATE TABLE items (
  id                INTEGER PRIMARY KEY,
  collection_id     INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  kind              TEXT NOT NULL DEFAULT 'place',
  merge_key         TEXT NOT NULL,              -- stable identity so ids survive rebuilds
  canonical_id      TEXT,                       -- e.g. Google place id
  provider          TEXT,
  name              TEXT NOT NULL,
  address           TEXT,
  lat               REAL,
  lng               REAL,
  category          TEXT,
  blurb             TEXT,
  mention_count     INTEGER NOT NULL DEFAULT 0,
  last_mentioned_at TEXT,
  needs_review      INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0,1)),
  review_reason     TEXT CHECK (review_reason IS NULL OR review_reason IN ('geocode_failed','low_match','low_confidence')),
  created_at        TEXT NOT NULL,
  CHECK ((lat IS NULL) = (lng IS NULL))
);
CREATE UNIQUE INDEX ux_items_collection_merge_key ON items(collection_id, merge_key);
CREATE INDEX ix_items_collection ON items(collection_id);

CREATE TABLE item_mentions (
  id                    INTEGER PRIMARY KEY,
  reel_id               INTEGER NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
  item_id               INTEGER REFERENCES items(id) ON DELETE SET NULL,
  kind                  TEXT NOT NULL DEFAULT 'place',
  raw_name              TEXT NOT NULL,
  raw_area              TEXT,
  raw_city_country      TEXT,
  raw_category          TEXT,
  raw_blurb             TEXT,
  confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  resolution_status     TEXT NOT NULL DEFAULT 'pending' CHECK (resolution_status IN ('pending','resolved','unresolved','skipped')),
  resolution_reason     TEXT,                   -- 'geocode_failed' | 'low_match'
  match_score           REAL,
  resolved_provider     TEXT,
  resolved_canonical_id TEXT,
  resolved_name         TEXT,
  resolved_address      TEXT,
  resolved_lat          REAL,
  resolved_lng          REAL,
  created_at            TEXT NOT NULL
);
CREATE INDEX ix_mentions_reel ON item_mentions(reel_id);
CREATE INDEX ix_mentions_item ON item_mentions(item_id);
CREATE INDEX ix_mentions_status ON item_mentions(resolution_status);

CREATE TABLE geocode_cache (
  key         TEXT PRIMARY KEY,
  provider    TEXT NOT NULL,
  result_json TEXT,                              -- NULL = negative result
  fetched_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);

CREATE TABLE outbox (
  id         INTEGER PRIMARY KEY,
  chat_id    INTEGER NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('text','document')),
  payload    TEXT NOT NULL,                      -- JSON
  dedupe_key TEXT UNIQUE,
  created_at TEXT NOT NULL,
  send_after TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  sent_at    TEXT,
  failed_at  TEXT,
  last_error TEXT
);
CREATE INDEX ix_outbox_pending ON outbox(sent_at, failed_at, send_after);
