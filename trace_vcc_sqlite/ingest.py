"""Ingest Claude Code JSONL log files into SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from trace_vcc_sqlite.textextract import extract_text

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_FTS_PATH = Path(__file__).parent / "fts.sql"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _backfill_text(conn: sqlite3.Connection) -> None:
    """Fill traces.text for rows ingested before the column existed."""
    rows = conn.execute("SELECT id, content FROM traces WHERE text IS NULL")
    updates = []
    for row_id, content in rows:
        try:
            text = extract_text(json.loads(content))
        except json.JSONDecodeError:
            continue
        if text:
            updates.append((text, row_id))
    conn.executemany("UPDATE traces SET text = ? WHERE id = ?", updates)


def _init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the database, apply schema and migrate old databases."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_PATH.read_text())

    # Databases created before full-text search lack the text column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(traces)")}
    if "text" not in cols:
        conn.execute("ALTER TABLE traces ADD COLUMN text TEXT")

    fts_existed = _has_table(conn, "traces_fts")
    conn.executescript(_FTS_PATH.read_text())
    if not fts_existed:
        # Triggers keep the index in sync from here on; fill it once for
        # whatever is already in the table.
        _backfill_text(conn)
        conn.execute("INSERT INTO traces_fts(traces_fts) VALUES ('rebuild')")
    conn.commit()
    return conn


def _extract_tool_name(record: dict) -> str | None:
    """Extract tool name from assistant tool_use blocks or tool_result records."""
    # Assistant message with tool_use blocks
    msg = record.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, list):
        names = [
            b.get("name")
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if names:
            return ",".join(names)
    # tool_result type records
    if record.get("type") == "result":
        tool_name = record.get("tool_name")
        if tool_name:
            return tool_name
    # toolUseResult records
    tur = record.get("toolUseResult")
    if isinstance(tur, dict):
        return tur.get("toolName")
    return None


def _extract_model(record: dict) -> str | None:
    """Extract model name from assistant messages."""
    return record.get("message", {}).get("model")


def ingest_file(conn: sqlite3.Connection, jsonl_path: str) -> int:
    """Ingest a single JSONL file into the database.

    Returns the number of rows inserted.
    """
    jsonl_path = os.path.abspath(jsonl_path)
    rows = []

    with open(jsonl_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            session_id = (
                record.get("sessionId")
                or record.get("session_id")
                or Path(jsonl_path).stem
            )
            rows.append((
                session_id,
                line_num,
                record.get("type"),
                record.get("subtype"),
                record.get("timestamp"),
                record.get("uuid"),
                record.get("parentUuid") or record.get("parent_uuid"),
                _extract_tool_name(record),
                _extract_model(record),
                line,  # raw JSON string
                extract_text(record),
                jsonl_path,
            ))

    if not rows:
        return 0

    # Re-ingesting a file replaces its rows. A plain DELETE (rather than
    # INSERT OR REPLACE) fires the triggers that keep the search index in sync.
    conn.execute("DELETE FROM traces WHERE source_file = ?", (jsonl_path,))
    conn.executemany(
        """INSERT INTO traces
           (session_id, line_num, type, subtype, timestamp, uuid, parent_uuid,
            tool_name, model, content, text, source_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def ingest_path(db_path: str, target: str) -> dict:
    """Ingest a JSONL file or all JSONL files in a directory.

    Returns dict with 'files' count and 'rows' count.
    """
    conn = _init_db(db_path)
    target_path = Path(target)
    total_files = 0
    total_rows = 0

    try:
        if target_path.is_file():
            n = ingest_file(conn, str(target_path))
            total_files = 1
            total_rows = n
        elif target_path.is_dir():
            for jsonl_file in sorted(target_path.glob("**/*.jsonl")):
                n = ingest_file(conn, str(jsonl_file))
                total_files += 1
                total_rows += n
        else:
            raise FileNotFoundError(f"Not found: {target}")
    finally:
        conn.close()

    return {"files": total_files, "rows": total_rows}
