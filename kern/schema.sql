-- Jarvis SQLite Schema

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Facts: persistent knowledge with quality gate ──────────────────────────
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'general',
    fact TEXT NOT NULL UNIQUE,
    importance INTEGER DEFAULT 5,
    source TEXT DEFAULT 'user',
    embedding BLOB,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);

-- ── Messages: append-only, untruncated ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);

-- ── Archives: conversation summaries with embeddings ───────────────────────
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    messages TEXT,
    embedding BLOB,
    decisions TEXT NOT NULL DEFAULT '[]',
    period_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    period_end TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_archives_created ON archives(created_at DESC);

-- ── Tools ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    script_path TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tools_name ON tools(name);

-- ── Config ─────────────────────────────────────────────────────────────────
-- (already defined above)
