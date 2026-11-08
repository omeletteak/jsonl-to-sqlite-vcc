"""Full-text search over ingested traces (SQLite FTS5, trigram tokenizer)."""

from __future__ import annotations

import re
import shlex
import sqlite3
from dataclasses import dataclass

# Trigram FTS can only match terms of 3+ characters. Shorter terms are
# matched with LIKE instead.
_MIN_FTS_TERM = 3

HIT_START = "\x02"
HIT_END = "\x03"


@dataclass
class SearchHit:
    id: int
    session_id: str
    line_num: int
    type: str | None
    timestamp: str | None
    tool_name: str | None
    snippet: str


@dataclass
class SessionHit:
    session_id: str
    hits: int
    first_ts: str | None
    last_ts: str | None
    snippet: str


def split_terms(query: str) -> list[str]:
    """Split a query into terms. Double quotes keep a phrase together."""
    try:
        terms = shlex.split(query)
    except ValueError:  # unbalanced quotes
        terms = query.split()
    return [t for t in terms if t.strip()]


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_where(
    terms: list[str],
    fts_query: str | None,
    session: str | None,
    types: tuple[str, ...],
    tool: str | None,
    since: str | None,
    until: str | None,
) -> tuple[str | None, list[str], list]:
    """Return (fts MATCH expression or None, WHERE clauses, params)."""
    clauses: list[str] = ["t.text IS NOT NULL"]
    params: list = []

    if fts_query is None:
        long_terms = [t for t in terms if len(t) >= _MIN_FTS_TERM]
        short_terms = [t for t in terms if len(t) < _MIN_FTS_TERM]
        fts_query = " ".join(_fts_phrase(t) for t in long_terms) or None
        for t in short_terms:
            clauses.append("t.text LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(t)}%")

    if session:
        clauses.append("t.session_id LIKE ? ESCAPE '\\'")
        params.append(f"{_escape_like(session)}%")
    if types:
        clauses.append(f"t.type IN ({','.join('?' * len(types))})")
        params.extend(types)
    if tool:
        clauses.append("t.tool_name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(tool)}%")
    if since:
        clauses.append("t.timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("t.timestamp < ?")
        params.append(until)

    return fts_query, clauses, params


def _make_snippet(text: str, terms: list[str], width: int = 160) -> str:
    """Cut a window of text around the first matching term and mark matches."""
    flat = re.sub(r"\s+", " ", text).strip()
    lowered = flat.lower()
    pos = -1
    for t in terms:
        p = lowered.find(t.lower())
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        pos = 0
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    window = flat[start:end]
    if terms:
        pattern = re.compile(
            "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)),
            re.IGNORECASE,
        )
        window = pattern.sub(lambda m: HIT_START + m.group(0) + HIT_END, window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return prefix + window + suffix


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    raw_fts: bool = False,
    session: str | None = None,
    types: tuple[str, ...] = (),
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sort: str = "rank",
    limit: int = 20,
) -> list[SearchHit]:
    """Search trace text. Terms are AND-ed; matching is case-insensitive.

    With raw_fts=True, `query` is passed to FTS5 MATCH as-is, so FTS5
    syntax (OR, NOT, NEAR, prefix*) is available.
    """
    terms = [] if raw_fts else split_terms(query)
    if not raw_fts and not terms:
        return []
    fts_query, clauses, params = _build_where(
        terms, query if raw_fts else None, session, types, tool, since, until
    )

    cols = "t.id, t.session_id, t.line_num, t.type, t.timestamp, t.tool_name, t.text"
    if fts_query:
        sql = (
            f"SELECT {cols}, bm25(traces_fts) AS score FROM traces_fts "
            "JOIN traces t ON t.id = traces_fts.rowid "
            "WHERE traces_fts MATCH ? AND " + " AND ".join(clauses)
        )
        params = [fts_query, *params]
        order = "score" if sort == "rank" else "t.timestamp DESC"
    else:
        sql = f"SELECT {cols}, 0 AS score FROM traces t WHERE " + " AND ".join(clauses)
        order = "t.timestamp DESC"
    sql += f" ORDER BY {order}, t.id LIMIT ?"
    params.append(limit)

    snippet_terms = terms or _terms_from_fts(query)
    hits = []
    for row in conn.execute(sql, params):
        hits.append(SearchHit(
            id=row[0], session_id=row[1], line_num=row[2], type=row[3],
            timestamp=row[4], tool_name=row[5],
            snippet=_make_snippet(row[6], snippet_terms),
        ))
    return hits


def _terms_from_fts(query: str) -> list[str]:
    """Best-effort terms from a raw FTS5 query, for highlighting only."""
    words = re.findall(r'"([^"]+)"|(\S+)', query)
    out = []
    for quoted, bare in words:
        w = (quoted or bare).rstrip("*")
        if w and w.upper() not in {"AND", "OR", "NOT", "NEAR"}:
            out.append(w)
    return out


def search_sessions(
    conn: sqlite3.Connection, query: str, *, limit: int = 20, **kwargs
) -> list[SessionHit]:
    """Group search hits by session, most hits first."""
    kwargs.pop("sort", None)
    hits = search(conn, query, sort="rank", limit=10_000, **kwargs)
    grouped: dict[str, SessionHit] = {}
    for h in hits:
        g = grouped.get(h.session_id)
        if g is None:
            grouped[h.session_id] = SessionHit(
                h.session_id, 1, h.timestamp, h.timestamp, h.snippet
            )
            continue
        g.hits += 1
        if h.timestamp:
            if not g.first_ts or h.timestamp < g.first_ts:
                g.first_ts = h.timestamp
            if not g.last_ts or h.timestamp > g.last_ts:
                g.last_ts = h.timestamp
    ranked = sorted(grouped.values(), key=lambda s: s.last_ts or "", reverse=True)
    ranked.sort(key=lambda s: s.hits, reverse=True)  # stable: ties stay newest-first
    return ranked[:limit]
