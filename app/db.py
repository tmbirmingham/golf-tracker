"""
SQLite storage for course reference data and saved rounds.

Migrations are additive (CREATE ... IF NOT EXISTS) and recorded in
schema_migrations, so running init_db() repeatedly is safe and never drops data.
"""
import os
import sqlite3

from flask import current_app, g

# Each entry is (version, sql). Only ever append new versions; never edit or
# remove an applied one.
MIGRATIONS = [
    (
        1,
        """
        -- Official / reference course data. Never written by round entry.
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            address TEXT,
            city TEXT,
            state TEXT,
            phone TEXT,
            website TEXT,
            opened_year INTEGER,
            description TEXT,
            published_par INTEGER,
            -- JSON: {field: {"status": "verified|unverified|user_entered", "source": ..., "checked_on": ..., "note": ...}}
            provenance TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS course_holes (
            course_id INTEGER NOT NULL REFERENCES courses(id),
            hole_number INTEGER NOT NULL,
            par INTEGER NOT NULL,
            handicap INTEGER,
            PRIMARY KEY (course_id, hole_number)
        );

        CREATE TABLE IF NOT EXISTS tees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL REFERENCES courses(id),
            name TEXT NOT NULL,
            color TEXT,
            gender TEXT,
            course_rating REAL,
            slope_rating INTEGER,
            published_par INTEGER,
            published_yardage INTEGER,
            status TEXT NOT NULL DEFAULT 'unverified',
            source TEXT,
            UNIQUE (course_id, name)
        );

        CREATE TABLE IF NOT EXISTS tee_holes (
            tee_id INTEGER NOT NULL REFERENCES tees(id),
            hole_number INTEGER NOT NULL,
            yardage INTEGER,
            -- Overrides course_holes.par / handicap for this tee when set
            par INTEGER,
            handicap INTEGER,
            PRIMARY KEY (tee_id, hole_number)
        );

        -- The player's rounds. course/tee details are snapshotted at the time
        -- of play so later course updates don't rewrite history.
        CREATE TABLE IF NOT EXISTS rounds (
            id TEXT PRIMARY KEY,
            course_id INTEGER REFERENCES courses(id),
            tee_id INTEGER REFERENCES tees(id),
            course_name TEXT NOT NULL,
            tee_name TEXT,
            course_snapshot TEXT NOT NULL,
            played_on TEXT NOT NULL DEFAULT (date('now')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS round_holes (
            round_id TEXT NOT NULL REFERENCES rounds(id),
            hole_number INTEGER NOT NULL,
            score INTEGER NOT NULL,
            putts INTEGER,
            gir INTEGER,
            fairway TEXT,
            PRIMARY KEY (round_id, hole_number)
        );
        """,
    ),
]


def database_path():
    path = current_app.config.get("DATABASE_PATH")
    if not path:
        path = os.path.join(current_app.instance_path, "golf_tracker.db")
    return path


def connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(database_path())
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate(conn):
    """Apply any migrations not yet recorded. Safe to run repeatedly."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + f"\nINSERT OR IGNORE INTO schema_migrations (version) VALUES ({version});\nCOMMIT;"
        )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        conn = connect(database_path())
        try:
            migrate(conn)
            # Seed bundled course files that aren't in the database yet.
            # Existing courses are left untouched; updates are deliberate
            # via `flask courses import --update`.
            from app.courses import seed_bundled_courses
            seed_bundled_courses(conn)
        finally:
            conn.close()
