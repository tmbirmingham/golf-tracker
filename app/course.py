"""
Helpers for the course being played in the current round.

Official course details live in the database (see app/courses.py and
app/data/courses/). When a round starts, the chosen course and tee are copied
into the session so the round is unaffected by later course updates.
"""
from flask import session

from app.courses import build_snapshot, course_info_from_snapshot
from app.db import get_db

DEFAULT_COURSE_SLUG = "arrowhead-golf-club-naples"


def default_snapshot():
    db = get_db()
    row = db.execute("SELECT id FROM courses WHERE slug = ?", (DEFAULT_COURSE_SLUG,)).fetchone()
    if row is None:
        row = db.execute("SELECT id FROM courses ORDER BY id LIMIT 1").fetchone()
    return build_snapshot(db, row["id"]) if row else None


def current_snapshot():
    """Snapshot for the round in progress (older sessions fall back to the default course)."""
    snapshot = session.get("course")
    if snapshot is None:
        snapshot = default_snapshot()
        session["course"] = snapshot
    return snapshot


def get_course_info():
    return course_info_from_snapshot(current_snapshot())


def hole_count():
    return len(current_snapshot()["holes"])


def get_hole(hole):
    for h in current_snapshot()["holes"]:
        if h["number"] == hole:
            return h
    return None


def get_hole_par(hole):
    h = get_hole(hole)
    return h["par"] if h else 4


def is_par_3(hole):
    return get_hole_par(hole) == 3
