#!/usr/bin/env python3
"""Benchmark: SQLite-backed queries vs raw JSONL scanning.

Two comparison modes:
  Part 1 — Real data: actual JSONL logs from ~/.claude/projects
  Part 2 — Scale test: synthetic data at 1K / 5K / 10K / 50K rows

Raw JSONL is tested two ways:
  - "Raw (disk)": re-read & parse all files from disk each time
  - "Raw (mem)":  pre-loaded in memory (best case for JSONL)
"""

import json
import os
import random
import sqlite3
import string
import tempfile
import time
from glob import glob
from pathlib import Path

from trace_vcc_sqlite.ingest import ingest_path, _init_db, ingest_file
from trace_vcc_sqlite.adapter import export_jsonl


# ── Timing ──

def timeit(fn, n=20):
    times = []
    result = None
    for _ in range(n):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    avg = sum(times) / len(times) * 1000
    return avg, result


# ═══════════════════════════════════════════════
# Part 1: Real data
# ═══════════════════════════════════════════════

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
ALL_JSONL = sorted(glob(f"{PROJECTS_DIR}/**/*.jsonl", recursive=True))

total_lines = sum(1 for f in ALL_JSONL for _ in open(f))
total_kb = sum(os.path.getsize(f) for f in ALL_JSONL) / 1024

print("=" * 72)
print("Part 1: Real data")
print(f"  {len(ALL_JSONL)} files, {total_lines} lines, {total_kb:.0f} KB")
print("=" * 72)
print()


# ── Raw JSONL ──

def raw_load_all():
    records = []
    for f in ALL_JSONL:
        with open(f, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if line:
                    records.append((f, i, json.loads(line)))
    return records


def raw_filter_type_disk(type_val):
    return [r for r in raw_load_all() if r[2].get("type") == type_val]


def raw_filter_tool_disk(tool_name):
    results = []
    for f, i, rec in raw_load_all():
        msg = rec.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    if b.get("name") == tool_name:
                        results.append((f, i, rec))
                        break
    return results


def raw_group_by_type_disk():
    counts = {}
    for _, _, rec in raw_load_all():
        t = rec.get("type")
        counts[t] = counts.get(t, 0) + 1
    return counts


# Pre-loaded variant
cached_data = raw_load_all()


def raw_filter_type_mem(type_val):
    return [r for r in cached_data if r[2].get("type") == type_val]


def raw_group_by_type_mem():
    counts = {}
    for _, _, rec in cached_data:
        t = rec.get("type")
        counts[t] = counts.get(t, 0) + 1
    return counts


# ── SQLite ──

DB_PATH = tempfile.mktemp(suffix=".db")
ingest_path(DB_PATH, PROJECTS_DIR)
db_kb = os.path.getsize(DB_PATH) / 1024
print(f"  DB size: {db_kb:.0f} KB")
print()


def sq_count():
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    conn.close()
    return n


def sq_filter_type(type_val):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT content FROM traces WHERE type = ?", (type_val,)).fetchall()
    conn.close()
    return rows


def sq_filter_tool(tool_name):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT content FROM traces WHERE tool_name = ?", (tool_name,)).fetchall()
    conn.close()
    return rows


def sq_group_by_type():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT type, COUNT(*) FROM traces GROUP BY type").fetchall()
    conn.close()
    return dict(rows)


# ── Run Part 1 ──

header = f"{'Operation':<28} {'Raw(disk)':>11} {'Raw(mem)':>11} {'SQLite':>11} {'disk/sq':>8} {'mem/sq':>8}"
print(header)
print("-" * len(header))

benchmarks_p1 = [
    ("Count all",
     lambda: len(raw_load_all()),
     lambda: len(cached_data),
     sq_count),
    ("Filter type='assistant'",
     lambda: raw_filter_type_disk("assistant"),
     lambda: raw_filter_type_mem("assistant"),
     lambda: sq_filter_type("assistant")),
    ("Filter tool='Read'",
     lambda: raw_filter_tool_disk("Read"),
     None,
     lambda: sq_filter_tool("Read")),
    ("GROUP BY type",
     raw_group_by_type_disk,
     raw_group_by_type_mem,
     sq_group_by_type),
]

for label, fn_disk, fn_mem, fn_sq in benchmarks_p1:
    disk_ms, _ = timeit(fn_disk)
    mem_ms = timeit(fn_mem)[0] if fn_mem else None
    sq_ms, _ = timeit(fn_sq)
    d_ratio = f"{disk_ms / sq_ms:.1f}x" if sq_ms > 0 else "—"
    m_ratio = f"{mem_ms / sq_ms:.1f}x" if mem_ms and sq_ms > 0 else "—"
    mem_str = f"{mem_ms:.2f} ms" if mem_ms else "—"
    print(f"{label:<28} {disk_ms:>8.2f} ms {mem_str:>11} {sq_ms:>8.2f} ms {d_ratio:>8} {m_ratio:>8}")

# Multi-query (realistic usage: 5 queries in a row)
def raw_multi():
    d = raw_load_all()
    [r for r in d if r[2].get("type") == "assistant"]
    [r for r in d if r[2].get("type") == "user"]
    [r for r in d if r[2].get("type") == "system"]
    counts = {}
    for _, _, rec in d:
        counts[rec.get("type")] = counts.get(rec.get("type"), 0) + 1


def sq_multi():
    sq_filter_type("assistant")
    sq_filter_type("user")
    sq_filter_type("system")
    sq_group_by_type()

disk_ms, _ = timeit(raw_multi)
sq_ms, _ = timeit(sq_multi)
print(f"{'4 queries in sequence':<28} {disk_ms:>8.2f} ms {'—':>11} {sq_ms:>8.2f} ms {disk_ms/sq_ms:>7.1f}x {'—':>8}")

os.unlink(DB_PATH)
print()

# ═══════════════════════════════════════════════
# Part 2: Scale test (synthetic data)
# ═══════════════════════════════════════════════

TYPES = ["user", "assistant", "system", "progress", "result", "file-history-snapshot"]
TOOLS = ["Read", "Write", "Bash", "Edit", "Grep", "Glob", "Agent", None, None, None]


def gen_record(i):
    t = random.choice(TYPES)
    tool = random.choice(TOOLS) if t == "assistant" else None
    rec = {
        "type": t,
        "uuid": f"uuid-{i}",
        "parentUuid": f"uuid-{max(0,i-1)}",
        "timestamp": f"2026-01-01T00:{i//60:02d}:{i%60:02d}.000Z",
        "sessionId": "bench-session",
        "message": {
            "content": [{"type": "text", "text": "x" * random.randint(50, 500)}],
        },
    }
    if tool:
        rec["message"]["content"].append({"type": "tool_use", "name": tool, "id": f"tid-{i}", "input": {}})
    return rec


def make_synthetic(n_rows):
    path = tempfile.mktemp(suffix=".jsonl")
    with open(path, "w") as f:
        for i in range(n_rows):
            f.write(json.dumps(gen_record(i)) + "\n")
    return path


print("=" * 72)
print("Part 2: Scale test (synthetic data)")
print("=" * 72)
print()

SCALE_SIZES = [1_000, 5_000, 10_000, 50_000]

header2 = f"{'Rows':>8} {'Op':<24} {'Raw(disk)':>11} {'SQLite':>11} {'Speedup':>9}"
print(header2)
print("-" * len(header2))

for n in SCALE_SIZES:
    jsonl_path = make_synthetic(n)
    db_path = tempfile.mktemp(suffix=".db")
    ingest_path(db_path, jsonl_path)

    def _raw_load():
        recs = []
        with open(jsonl_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        return recs

    def _raw_filter():
        return [r for r in _raw_load() if r.get("type") == "assistant"]

    def _raw_group():
        counts = {}
        for r in _raw_load():
            t = r.get("type")
            counts[t] = counts.get(t, 0) + 1
        return counts

    def _sq_filter():
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT content FROM traces WHERE type = ?", ("assistant",)).fetchall()
        conn.close()
        return rows

    def _sq_group():
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT type, COUNT(*) FROM traces GROUP BY type").fetchall()
        conn.close()
        return dict(rows)

    iters = max(5, 30 // (n // 1000))

    for op_name, fn_raw, fn_sq in [
        ("Filter type", _raw_filter, _sq_filter),
        ("GROUP BY type", _raw_group, _sq_group),
    ]:
        raw_ms, _ = timeit(fn_raw, iters)
        sq_ms, _ = timeit(fn_sq, iters)
        ratio = raw_ms / sq_ms if sq_ms > 0 else float("inf")
        print(f"{n:>8,} {op_name:<24} {raw_ms:>8.2f} ms {sq_ms:>8.2f} ms {ratio:>8.1f}x")

    os.unlink(jsonl_path)
    os.unlink(db_path)

print()
print("Key takeaways:")
print("  - Small data (<1K rows): SQLite overhead ~= raw JSONL parse time")
print("  - Large data (10K+ rows): SQLite indexed queries pull ahead significantly")
print("  - Real win: repeated/multiple queries — ingest once, query many times")
print("  - SQLite also enables SQL-level filtering before passing to VCC")
