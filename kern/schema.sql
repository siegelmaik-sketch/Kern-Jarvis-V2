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
    args_schema TEXT,  -- JSON list of arg names extracted from main(), e.g. '["query","max_results"]'
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tools_name ON tools(name);

-- ── MCP Servers ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    headers TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_name ON mcp_servers(name);

-- ── Web Cache ──────────────────────────────────────────────────────────────
-- Caches SearXNG query results for a configurable TTL (default 1h).
-- Composite key (query, language) so DE/EN versions of the same query
-- are stored separately.
CREATE TABLE IF NOT EXISTS web_cache (
    query TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'de',
    results_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (query, language)
);

CREATE INDEX IF NOT EXISTS idx_web_cache_created ON web_cache(created_at DESC);

-- ── Config ─────────────────────────────────────────────────────────────────
-- (already defined above)
