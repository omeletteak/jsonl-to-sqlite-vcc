"""CLI entry point for trace-vcc-sqlite."""

import click

from trace_vcc_sqlite.ingest import ingest_path
from trace_vcc_sqlite.adapter import export_jsonl, run_vcc, query_traces


@click.group()
@click.version_option(package_name="trace-vcc-sqlite")
def cli():
    """Ingest Claude Code JSONL logs into SQLite and generate VCC views."""


@cli.command()
@click.argument("db_path", type=click.Path())
@click.argument("target", type=click.Path(exists=True))
def ingest(db_path, target):
    """Ingest JSONL file(s) into SQLite.

    TARGET can be a single .jsonl file or a directory (recursive).
    """
    result = ingest_path(db_path, target)
    click.echo(
        f"Ingested {result['rows']} rows from {result['files']} file(s) into {db_path}"
    )


@cli.command()
@click.argument("db_path", type=click.Path(exists=True))
@click.option("-w", "--where", default="", help="SQL WHERE clause for filtering.")
@click.option("-p", "--param", multiple=True, help="Parameters for WHERE clause.")
@click.option("-o", "--output-dir", default=None, help="Output directory for VCC files.")
@click.option("--grep", default=None, help="Pass --grep pattern to VCC.")
@click.option("-t", "--truncate", default=None, type=int, help="Token truncation limit.")
def view(db_path, where, param, output_dir, grep, truncate):
    """Filter traces and generate VCC views.

    Examples:

      trace-vcc view traces.db -w "type = 'assistant'"

      trace-vcc view traces.db -w "tool_name LIKE ?" -p "%Read%" --grep "error"

      trace-vcc view traces.db -w "session_id = ?" -p "abc123"
    """
    vcc_args = []
    if grep:
        vcc_args.extend(["--grep", grep])
    if truncate is not None:
        vcc_args.extend(["-t", str(truncate)])

    files = run_vcc(
        db_path,
        where=where,
        params=tuple(param),
        vcc_args=vcc_args or None,
        output_dir=output_dir,
    )
    if files:
        click.echo("Generated:")
        for f in files:
            click.echo(f"  {f}")
    else:
        click.echo("No matching traces found.")


@cli.command()
@click.argument("db_path", type=click.Path(exists=True))
@click.argument("output", type=click.Path())
@click.option("-w", "--where", default="", help="SQL WHERE clause for filtering.")
@click.option("-p", "--param", multiple=True, help="Parameters for WHERE clause.")
def export(db_path, output, where, param):
    """Export filtered traces back to JSONL.

    Examples:

      trace-vcc export traces.db out.jsonl -w "type IN ('user','assistant')"
    """
    n = export_jsonl(db_path, output, where=where, params=tuple(param))
    click.echo(f"Exported {n} lines to {output}")


@cli.command()
@click.argument("db_path", type=click.Path(exists=True))
@click.option("--sql", "raw_sql", default=None, help="Raw SQL query (SELECT only).")
def query(db_path, raw_sql):
    """Query the traces database.

    Without --sql, shows summary stats. With --sql, runs the query.

    Examples:

      trace-vcc query traces.db

      trace-vcc query traces.db --sql "SELECT type, count(*) FROM traces GROUP BY type"
    """
    import sqlite3

    conn = sqlite3.connect(db_path)

    if raw_sql:
        if not raw_sql.strip().upper().startswith("SELECT"):
            click.echo("Error: Only SELECT queries are allowed.", err=True)
            raise SystemExit(1)
        cursor = conn.execute(raw_sql)
        cols = [d[0] for d in cursor.description]
        click.echo("\t".join(cols))
        for row in cursor:
            click.echo("\t".join(str(v) for v in row))
    else:
        # Default: show summary
        for label, sql in [
            ("Sessions", "SELECT COUNT(DISTINCT session_id) FROM traces"),
            ("Total rows", "SELECT COUNT(*) FROM traces"),
            ("Source files", "SELECT COUNT(DISTINCT source_file) FROM traces"),
        ]:
            val = conn.execute(sql).fetchone()[0]
            click.echo(f"{label}: {val}")

        click.echo("\nBy type:")
        for row in conn.execute(
            "SELECT type, COUNT(*) as cnt FROM traces GROUP BY type ORDER BY cnt DESC"
        ):
            click.echo(f"  {row[0] or '(null)'}: {row[1]}")

        click.echo("\nBy tool:")
        for row in conn.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM traces "
            "WHERE tool_name IS NOT NULL GROUP BY tool_name ORDER BY cnt DESC LIMIT 15"
        ):
            click.echo(f"  {row[0]}: {row[1]}")

    conn.close()
