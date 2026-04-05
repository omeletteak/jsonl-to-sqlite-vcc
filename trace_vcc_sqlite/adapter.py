"""Adapter: query SQLite and reconstruct JSONL for VCC processing."""

import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def query_traces(db_path: str, where: str = "", params: tuple = ()) -> list[dict]:
    """Query traces and return parsed JSON records in line_num order.

    Args:
        db_path: Path to SQLite database.
        where: SQL WHERE clause (without 'WHERE' keyword). e.g. "type = ?"
        params: Parameters for the WHERE clause.

    Returns:
        List of (line_num, parsed_json) tuples.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = "SELECT line_num, content FROM traces"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY session_id, line_num"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [(row["line_num"], json.loads(row["content"])) for row in rows]


def export_jsonl(db_path: str, output_path: str,
                 where: str = "", params: tuple = ()) -> int:
    """Export filtered traces back to a JSONL file (VCC-compatible).

    Returns the number of lines written.
    """
    rows = query_traces(db_path, where, params)
    with open(output_path, "w", encoding="utf-8") as f:
        for _line_num, record in rows:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(rows)


def _find_vcc() -> str:
    """Locate VCC.py script."""
    # Check in the repo's skill directory
    candidates = [
        Path(__file__).parent.parent / ".claude" / "skills" / "conversation-compiler" / "scripts" / "VCC.py",
        Path.home() / ".claude" / "skills" / "conversation-compiler" / "scripts" / "VCC.py",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    raise FileNotFoundError(
        "VCC.py not found. Ensure the conversation-compiler skill is installed."
    )


def run_vcc(db_path: str, where: str = "", params: tuple = (),
            vcc_args: list[str] | None = None,
            output_dir: str | None = None) -> list[str]:
    """Export filtered traces to temp JSONL, run VCC, return output file paths.

    Args:
        db_path: Path to SQLite database.
        where: SQL WHERE clause for filtering.
        params: Parameters for WHERE clause.
        vcc_args: Extra arguments to pass to VCC.py (e.g. ['--grep', 'keyword']).
        output_dir: Output directory for VCC files. Defaults to temp dir.

    Returns:
        List of output file paths created by VCC.
    """
    vcc_path = _find_vcc()

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="trace_vcc_")
    os.makedirs(output_dir, exist_ok=True)

    # Export filtered data to temp JSONL
    tmp_jsonl = os.path.join(output_dir, "filtered.jsonl")
    count = export_jsonl(db_path, tmp_jsonl, where, params)
    if count == 0:
        return []

    # Run VCC
    cmd = ["python3", vcc_path, tmp_jsonl, "-o", output_dir]
    if vcc_args:
        cmd.extend(vcc_args)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"VCC failed: {result.stderr}")

    # Collect output files
    output_files = []
    for ext in (".txt", ".min.txt", ".view.txt"):
        for f in Path(output_dir).glob(f"*{ext}"):
            output_files.append(str(f))

    # Print VCC stdout (file listing)
    if result.stdout.strip():
        print(result.stdout.strip())

    return sorted(output_files)
