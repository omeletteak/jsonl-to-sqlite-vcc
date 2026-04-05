"""CLI entry point for jsonl-to-sqlite."""

import click

from jsonl_to_sqlite.converter import insert_jsonl, create_table_from_jsonl


@cli.command()
@click.argument("db_path", type=click.Path())
@click.argument("table_name")
@click.argument("jsonl_file", type=click.File("r"), default="-")
@click.option("--batch-size", default=1000, help="Number of rows per INSERT batch.")
@click.option("--pk", default=None, help="Column to use as primary key.")
@click.option(
    "--if-exists",
    type=click.Choice(["fail", "replace", "append"]),
    default="append",
    help="Behavior when the table already exists.",
)
def insert(db_path, table_name, jsonl_file, batch_size, pk, if_exists):
    """Insert JSONL data into a SQLite table."""
    stats = insert_jsonl(
        db_path=db_path,
        table_name=table_name,
        jsonl_file=jsonl_file,
        batch_size=batch_size,
        pk=pk,
        if_exists=if_exists,
    )
    click.echo(f"Inserted {stats['rows']} rows into {table_name}")


@cli.command("create-table")
@click.argument("db_path", type=click.Path())
@click.argument("table_name")
@click.argument("jsonl_file", type=click.File("r"), default="-")
@click.option(
    "--detect-lines",
    default=100,
    help="Number of lines to read for schema detection.",
)
@click.option("--pk", default=None, help="Column to use as primary key.")
def create_table(db_path, table_name, jsonl_file, detect_lines, pk):
    """Create a table from JSONL schema without inserting data."""
    columns = create_table_from_jsonl(
        db_path=db_path,
        table_name=table_name,
        jsonl_file=jsonl_file,
        detect_lines=detect_lines,
        pk=pk,
    )
    click.echo(f"Created table {table_name} with columns: {', '.join(columns)}")


@click.group()
@click.version_option()
def cli():
    """Convert JSONL files to SQLite databases."""


cli.add_command(insert)
cli.add_command(create_table)
