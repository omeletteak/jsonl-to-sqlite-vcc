import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from trace_vcc_sqlite.cli import cli
from trace_vcc_sqlite.ingest import _SCHEMA_PATH, _init_db, ingest_path
from trace_vcc_sqlite.search import search, search_sessions
from trace_vcc_sqlite.textextract import extract_text

RECORDS_A = [
    {"type": "user", "sessionId": "sess-a", "timestamp": "2026-11-01T10:00:00Z",
     "message": {"role": "user", "content": "認証のバグを直したい"}},
    {"type": "assistant", "sessionId": "sess-a", "timestamp": "2026-11-01T10:00:05Z",
     "message": {"role": "assistant", "model": "m", "content": [
         {"type": "text", "text": "Looking at the migration error now."},
         {"type": "tool_use", "id": "t1", "name": "Bash",
          "input": {"command": "pytest tests/test_auth.py"}},
     ]}},
    {"type": "user", "sessionId": "sess-a", "timestamp": "2026-11-01T10:00:09Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "t1",
          "content": "FAILED test_login - KeyError: 'token'"},
     ]}},
    {"type": "queue-operation", "sessionId": "sess-a", "content": "認証 duplicate"},
]
RECORDS_B = [
    {"type": "user", "sessionId": "sess-b", "timestamp": "2026-11-05T09:00:00Z",
     "message": {"role": "user", "content": "Add a DB migration for users"}},
]


def _write(path: Path, records):
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def logs(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    _write(d / "a.jsonl", RECORDS_A)
    _write(d / "b.jsonl", RECORDS_B)
    return d


@pytest.fixture
def db(tmp_path, logs):
    path = tmp_path / "t.db"
    ingest_path(str(path), str(logs))
    conn = _init_db(str(path))
    yield conn
    conn.close()


def test_extract_text_covers_blocks():
    assert extract_text(RECORDS_A[0]) == "認証のバグを直したい"
    text = extract_text(RECORDS_A[1])
    assert "migration error" in text and "Bash" in text and "pytest tests" in text
    assert "KeyError" in extract_text(RECORDS_A[2])
    assert extract_text(RECORDS_A[3]) is None


def test_japanese_substring(db):
    hits = search(db, "認証")  # 2 chars: falls back to LIKE
    assert [h.line_num for h in hits] == [0]
    assert "[" not in hits[0].snippet  # markers are control chars, not brackets
    hits = search(db, "バグを直")  # 4 chars: trigram FTS
    assert len(hits) == 1 and hits[0].session_id == "sess-a"


def test_terms_are_anded_and_case_insensitive(db):
    assert {h.session_id for h in search(db, "MIGRATION")} == {"sess-a", "sess-b"}
    hits = search(db, "migration users")
    assert [h.session_id for h in hits] == ["sess-b"]


def test_filters(db):
    hits = search(db, "migration", since="2026-11-03")
    assert [h.session_id for h in hits] == ["sess-b"]
    hits = search(db, "migration", types=("assistant",))
    assert [h.type for h in hits] == ["assistant"]
    assert search(db, "keyerror", session="sess-a")
    assert not search(db, "keyerror", session="sess-b")
    assert [h.tool_name for h in search(db, "pytest", tool="bash")] == ["Bash"]


def test_raw_fts(db):
    hits = search(db, "keyerror OR users", raw_fts=True)
    assert {h.session_id for h in hits} == {"sess-a", "sess-b"}


def test_search_sessions(db):
    groups = search_sessions(db, "migration")
    assert {g.session_id: g.hits for g in groups} == {"sess-a": 1, "sess-b": 1}
    assert groups[0].session_id == "sess-b"  # tie on hits: newest first


def test_reingest_keeps_index_in_sync(tmp_path, logs):
    path = str(tmp_path / "t.db")
    ingest_path(path, str(logs))
    _write(logs / "b.jsonl", [{**RECORDS_B[0], "message": {"content": "renamed"}}])
    ingest_path(path, str(logs))
    conn = _init_db(path)
    assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 5
    assert [h.session_id for h in search(conn, "migration")] == ["sess-a"]
    assert search(conn, "renamed")
    conn.execute("INSERT INTO traces_fts(traces_fts) VALUES ('integrity-check')")


def test_old_database_is_migrated(tmp_path, logs):
    path = str(tmp_path / "old.db")
    old_schema = "\n".join(
        line for line in _SCHEMA_PATH.read_text().splitlines()
        if not line.strip().startswith("text ")
    )
    conn = sqlite3.connect(path)
    conn.executescript(old_schema)
    for i, r in enumerate(RECORDS_A):
        conn.execute(
            "INSERT INTO traces (session_id, line_num, type, content, source_file)"
            " VALUES (?, ?, ?, ?, 'a.jsonl')",
            (r["sessionId"], i, r["type"], json.dumps(r, ensure_ascii=False)),
        )
    conn.commit()
    conn.close()

    conn = _init_db(path)
    assert [h.line_num for h in search(conn, "KeyError")] == [2]


def test_cli_search(tmp_path, logs):
    path = str(tmp_path / "t.db")
    runner = CliRunner()
    assert runner.invoke(cli, ["ingest", path, str(logs)]).exit_code == 0

    res = runner.invoke(cli, ["search", path, "migration"])
    assert res.exit_code == 0, res.output
    assert "[migration]" in res.output.lower()

    res = runner.invoke(cli, ["search", path, "migration", "--sessions"])
    assert "sess-a  1 hit(s)" in res.output

    assert "No matches." in runner.invoke(cli, ["search", path, "zzzz"]).output

    res = runner.invoke(cli, ["search", path, '"bad', "--fts"])
    assert res.exit_code != 0 and "Search failed" in res.output
