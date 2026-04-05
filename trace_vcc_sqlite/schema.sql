-- trace-vcc-sqlite schema
-- Each row = one line from a Claude Code JSONL log file.
-- content stores the full JSON object as-is (no normalization).

CREATE TABLE IF NOT EXISTS traces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,           -- from JSONL sessionId or filename
    line_num    INTEGER NOT NULL,           -- 0-based line number in original file
    type        TEXT,                       -- record type: user, assistant, system, result, ...
    subtype     TEXT,                       -- e.g. compact_boundary, init
    timestamp   TEXT,                       -- ISO 8601 timestamp
    uuid        TEXT,                       -- message uuid
    parent_uuid TEXT,                       -- parent message uuid (conversation threading)
    tool_name   TEXT,                       -- extracted from tool_use blocks or tool_result
    model       TEXT,                       -- model name from assistant messages
    content     TEXT    NOT NULL,           -- full JSON object as string
    source_file TEXT    NOT NULL,           -- path of the original JSONL file
    ingested_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_traces_session    ON traces (session_id);
CREATE INDEX IF NOT EXISTS idx_traces_type       ON traces (type);
CREATE INDEX IF NOT EXISTS idx_traces_timestamp  ON traces (timestamp);
CREATE INDEX IF NOT EXISTS idx_traces_tool_name  ON traces (tool_name);
CREATE INDEX IF NOT EXISTS idx_traces_session_line ON traces (session_id, line_num);

-- Unique constraint: same file + same line = same record
CREATE UNIQUE INDEX IF NOT EXISTS idx_traces_source_line ON traces (source_file, line_num);
