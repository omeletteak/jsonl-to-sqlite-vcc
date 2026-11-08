-- Full-text search index over traces.text.
-- External-content FTS5 table: the text lives in traces, the index here.
-- The trigram tokenizer matches any substring of 3+ characters, so it works
-- for Japanese and code identifiers without word segmentation.

CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts USING fts5(
    text,
    content='traces',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS traces_fts_ai AFTER INSERT ON traces
WHEN new.text IS NOT NULL BEGIN
    INSERT INTO traces_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS traces_fts_ad AFTER DELETE ON traces
WHEN old.text IS NOT NULL BEGIN
    INSERT INTO traces_fts(traces_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS traces_fts_au AFTER UPDATE OF text ON traces BEGIN
    INSERT INTO traces_fts(traces_fts, rowid, text)
        SELECT 'delete', old.id, old.text WHERE old.text IS NOT NULL;
    INSERT INTO traces_fts(rowid, text)
        SELECT new.id, new.text WHERE new.text IS NOT NULL;
END;
