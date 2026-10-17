"""
Course data commands:

  flask courses validate [FILE ...]         check course files (sums, ranges)
  flask courses import [FILE ...]           add courses not yet in the database
  flask courses import --update [FILE ...]  also refresh existing courses from file

With no FILE, the bundled files in app/data/courses are used.
"""
import click
from flask.cli import AppGroup

from app.courses import (
    CourseDataError, bundled_course_files, import_course, load_course_file, validate_course_data,
)
from app.db import connect, database_path

courses_cli = AppGroup("courses", help="Manage course reference data.")


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
