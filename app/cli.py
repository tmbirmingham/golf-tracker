"""
Command-line tools (run with `flask <command>`):

  flask courses validate [FILE ...]         check course files (sums, ranges)
  flask courses import [FILE ...]           add courses not yet in the database
  flask courses import --update [FILE ...]  also refresh existing courses from file
  flask backup now                          merge with and write the GitHub backup
  flask backup export FILE                  write all rounds to a local JSON file
  flask backup import FILE                  add rounds from a JSON export (never overwrites)
  flask send-test-email ADDRESS             check the Mailjet configuration

With no FILE, the course commands use the bundled files in app/data/courses.
"""
import json

import click
from flask import current_app
from flask.cli import AppGroup

from app.courses import (
    CourseDataError, bundled_course_files, import_course, load_course_file, validate_course_data,
)
from app.db import connect, database_path

courses_cli = AppGroup("courses", help="Manage course reference data.")
backup_cli = AppGroup("backup", help="Back up and restore saved rounds.")


@courses_cli.command("validate")
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def validate_command(files):
    failed = False
    for path in files or bundled_course_files():
        errors = validate_course_data(load_course_file(path))
        if errors:
            failed = True
            click.echo(f"FAIL {path}")
            for e in errors:
                click.echo(f"  - {e}")
        else:
            click.echo(f"ok   {path}")
    if failed:
        raise SystemExit(1)


@courses_cli.command("import")
@click.option("--update", is_flag=True, help="Refresh courses that already exist (never deletes data).")
@click.argument("files", nargs=-1, type=click.Path(exists=True, dir_okay=False))
def import_command(update, files):
    conn = connect(database_path())
    try:
        for path in files or bundled_course_files():
            try:
                result = import_course(conn, load_course_file(path), update=update)
            except CourseDataError as e:
                click.echo(f"FAIL {path}")
                for err in e.errors:
                    click.echo(f"  - {err}")
                raise SystemExit(1)
            click.echo(f"{result:8} {path}")
    finally:
        conn.close()


@backup_cli.command("now")
def backup_now_command():
    from app.backup import backup_now
    conn = connect(database_path())
    try:
        if not current_app.config.get("BACKUP_GITHUB_TOKEN"):
            raise click.ClickException("BACKUP_GITHUB_TOKEN / BACKUP_GITHUB_REPO are not set.")
        if not backup_now(conn):
            raise click.ClickException("Backup failed; see the log above.")
        click.echo("Backup written.")
    finally:
        conn.close()


@backup_cli.command("export")
@click.argument("file", type=click.Path(dir_okay=False, writable=True))
def export_command(file):
    from app.rounds import export_rounds
    conn = connect(database_path())
    try:
        data = export_rounds(conn)
    finally:
        conn.close()
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    click.echo(f"Exported {len(data['rounds'])} rounds to {file}")


@backup_cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def import_rounds_command(file):
    from app.rounds import import_rounds
    with open(file, encoding="utf-8") as f:
        data = json.load(f)
    conn = connect(database_path())
    try:
        added = import_rounds(conn, data)
    finally:
        conn.close()
    click.echo(f"Added {added} rounds (existing rounds left unchanged).")


@click.command("send-test-email")
@click.argument("address")
def send_test_email_command(address):
    """Send a test email (replaces the old public /test-email page)."""
    from app.email_utils import send_test_email
    status, message = send_test_email(address)
    click.echo(f"{status}: {message}")
