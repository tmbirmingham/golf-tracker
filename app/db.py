"""
SQLite storage for course reference data and saved rounds.

Migrations only add tables, columns and indexes, and each one is recorded in
schema_migrations, so running them repeatedly is safe and never drops data.
"""
import json
import os
import sqlite3

from flask import current_app, g


def _backfill_round_totals(conn):
    """Fill the stored totals for rounds saved before migration 2."""
    rows = conn.execute(
        "SELECT id, course_snapshot FROM rounds WHERE holes_played IS NULL"
    ).fetchall()
    for r in rows:
        pars = {h["number"]: h["par"] for h in json.loads(r[1])["holes"]}
        holes = conn.execute(
            "SELECT hole_number, score FROM round_holes WHERE round_id = ?", (r[0],)
        ).fetchall()
        conn.execute(
            "UPDATE rounds SET holes_played = ?, total_score = ?, total_par = ? WHERE id = ?",
            (len(holes), sum(h[1] for h in holes), sum(pars.get(h[0], 0) for h in holes), r[0]),
        )


# Each entry is (version, sql or callable). Only ever append new versions; never edit or
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
    (
        2,
        """
        -- Player display name as entered, plus a normalized form for searching.
        -- A name is a label, not an identity: same-named players are not merged.
        ALTER TABLE rounds ADD COLUMN player_name TEXT;
        ALTER TABLE rounds ADD COLUMN player_key TEXT;
        -- Stored totals so the rounds list can sort and filter in SQL.
        ALTER TABLE rounds ADD COLUMN holes_played INTEGER;
        ALTER TABLE rounds ADD COLUMN total_score INTEGER;
        ALTER TABLE rounds ADD COLUMN total_par INTEGER;
        CREATE INDEX IF NOT EXISTS idx_rounds_played_on ON rounds (played_on);
        CREATE INDEX IF NOT EXISTS idx_rounds_player_key ON rounds (player_key);

        -- Version of the bundled course file last applied, so corrected course
        -- data can be rolled out by bumping data_version in the file.
        ALTER TABLE courses ADD COLUMN data_version INTEGER NOT NULL DEFAULT 1;

        -- Email sends, for rate limiting. Stores no email addresses; the
        -- client address is kept only as a salted hash.
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id TEXT,
            client_hash TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_email_log_sent_at ON email_log (sent_at);
        """,
    ),
    (3, _backfill_round_totals),
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


def _statements(sql):
    """Split a migration script into individual statements."""
    statements, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip():
        statements.append(buf.strip())
    return statements


def migrate(conn):
    """Apply any migrations not yet recorded. Safe to run repeatedly and from
    several processes at once: each version is re-checked inside its own
    write transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    for version, step in MIGRATIONS:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone():
                conn.rollback()
                continue
            if callable(step):
                step(conn)
            else:
                for statement in _statements(step):
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        conn = connect(database_path())
        try:
            migrate(conn)
            # Seed bundled course files that aren't in the database yet, and
            # apply a bundled file whose data_version is newer than the copy
            # in the database. Other changes are deliberate, via
            # `flask courses import --update`.
            from app.courses import seed_bundled_courses
            seed_bundled_courses(conn)
            # Bring back saved rounds after a redeploy wiped the local disk.
            from app.backup import restore_if_empty
            restore_if_empty(conn)
        finally:
            conn.close()
