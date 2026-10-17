"""
The players' saved rounds. Rounds store their own copy of the course/tee
details as played and never write to the course tables.

A player name is a display label, not an identity: rounds are grouped for
searching by a normalized form of the name, but nothing is merged or
authorized by name.
"""
import hashlib
import json
import re
import unicodedata

MAX_NAME_LENGTH = 40
PAGE_SIZE = 25


# --------------------------------------------------------------- player names

def clean_player_name(raw):
    """Display form: Unicode-normalized, single-spaced, trimmed, length-limited.
    Returns None if nothing usable is left."""
    name = unicodedata.normalize("NFKC", raw or "")
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:MAX_NAME_LENGTH] or None


def player_key(name):
    """Search key: case- and accent-insensitive form of a cleaned name."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# ----------------------------------------------------------------- saving

def _totals(snapshot, entries):
    pars = {h["number"]: h["par"] for h in snapshot["holes"]}
    return (
        len(entries),
        sum(e["score"] for e in entries),
        sum(pars.get(e["hole_number"], 0) for e in entries),
    )


def _upsert_round(conn, round_id, snapshot, entries, player_name, played_on):
    holes_played, total_score, total_par = _totals(snapshot, entries)
    conn.execute(
        "INSERT INTO rounds (id, course_id, tee_id, course_name, tee_name, course_snapshot, "
        "player_name, player_key, played_on, holes_played, total_score, total_par) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET player_name = excluded.player_name, "
        "player_key = excluded.player_key, played_on = excluded.played_on, "
        "holes_played = excluded.holes_played, total_score = excluded.total_score, "
        "total_par = excluded.total_par, updated_at = datetime('now')",
        (round_id, snapshot["course_id"], snapshot.get("tee_id"), snapshot["course_name"],
         snapshot.get("tee_name"), json.dumps(snapshot, sort_keys=True),
         player_name, player_key(player_name) if player_name else None, played_on,
         holes_played, total_score, total_par),
    )
    conn.execute("DELETE FROM round_holes WHERE round_id = ?", (round_id,))
    conn.executemany(
        "INSERT INTO round_holes (round_id, hole_number, score, putts, gir, fairway) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(round_id, e["hole_number"], e["score"], e.get("putts"),
          None if e.get("gir") is None else int(bool(e["gir"])), e.get("fairway"))
         for e in entries],
    )


def save_round(conn, round_id, snapshot, entries, player_name, played_on):
    """
    Insert or replace a round. entries: list of dicts with hole_number, score,
    putts, gir, fairway. Re-saving the same round_id (e.g. a double-submitted
    form) replaces it rather than creating a duplicate.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        _upsert_round(conn, round_id, snapshot, entries, player_name, played_on)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ----------------------------------------------------------------- reading

def round_filters(args):
    """Parse and sanitize list filters from query-string args."""
    view = args.get("view", "recent")
    if view not in ("recent", "best"):
        view = "recent"
    holes = args.get("holes", "")
    if holes not in ("", "9", "18"):
        holes = ""
    if view == "best" and not holes:
        # Ranking partial rounds against full ones would be misleading.
        holes = "18"
    try:
        page = max(1, int(args.get("page", 1)))
    except ValueError:
        page = 1

    def _int(name):
        try:
            return int(args.get(name, ""))
        except ValueError:
            return None

    def _date(name):
        value = args.get(name, "")
        return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else ""

    return {
        "view": view,
        "player": (clean_player_name(args.get("player", "")) or ""),
        "course": _int("course"),
        "tee": (args.get("tee", "") or "")[:40],
        "holes": holes,
        "date_from": _date("from"),
        "date_to": _date("to"),
        "page": page,
    }


def query_rounds(conn, f):
    """Rounds matching the filters, one page at a time. Returns (rows, total_count)."""
    where, params = [], []
    if f["player"]:
        where.append("player_key LIKE ? ESCAPE '\\'")
        key = player_key(f["player"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{key}%")
    if f["course"]:
        where.append("course_id = ?")
        params.append(f["course"])
    if f["tee"]:
        where.append("tee_name = ?")
        params.append(f["tee"])
    if f["holes"] == "18":
        where.append("holes_played = 18")
    elif f["holes"] == "9":
        where.append("holes_played = 9")
    if f["date_from"]:
        where.append("played_on >= ?")
        params.append(f["date_from"])
    if f["date_to"]:
        where.append("played_on <= ?")
        params.append(f["date_to"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    if f["view"] == "best":
        order = "(total_score - total_par) ASC, total_score ASC, played_on DESC, created_at DESC"
    else:
        order = "played_on DESC, created_at DESC"

    total = conn.execute(f"SELECT COUNT(*) FROM rounds{clause}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT id, player_name, course_name, tee_name, played_on, holes_played, "
        f"total_score, total_par FROM rounds{clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (f["page"] - 1) * PAGE_SIZE],
    ).fetchall()
    results = []
    offset = (f["page"] - 1) * PAGE_SIZE
    for i, r in enumerate(rows):
        d = dict(r)
        d["to_par"] = d["total_score"] - d["total_par"]
        # Tied rounds share a rank (1, 1, 3) on the leaderboard.
        prev = results[-1] if results else None
        if prev and (prev["to_par"], prev["total_score"]) == (d["to_par"], d["total_score"]):
            d["rank"] = prev["rank"]
        else:
            d["rank"] = offset + i + 1
        results.append(d)
    return results, total


def get_round(conn, round_id):
    """A saved round with its own course snapshot and hole scores, or None."""
    r = conn.execute(
        "SELECT id, player_name, course_name, tee_name, played_on, holes_played, total_score, "
        "total_par, course_snapshot FROM rounds WHERE id = ?",
        (round_id,),
    ).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["snapshot"] = json.loads(d.pop("course_snapshot"))
    d["to_par"] = d["total_score"] - d["total_par"]
    d["holes"] = {
        h["hole_number"]: dict(h)
        for h in conn.execute(
            "SELECT hole_number, score, putts, gir, fairway FROM round_holes WHERE round_id = ?",
            (round_id,),
        )
    }
    d["scores"] = {n: h["score"] for n, h in d["holes"].items()}
    return d


def player_names(conn, limit=200):
    """Distinct display names already used, for autocomplete."""
    return [r[0] for r in conn.execute(
        "SELECT player_name FROM rounds WHERE player_name IS NOT NULL "
        "GROUP BY player_name ORDER BY MAX(created_at) DESC LIMIT ?",
        (limit,),
    )]


def tee_names(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT tee_name FROM rounds WHERE tee_name IS NOT NULL ORDER BY tee_name"
    )]


# --------------------------------------------------------- export / import

def export_rounds(conn):
    """All rounds as a JSON-serializable dict. Course snapshots are stored once each."""
    snapshots, rounds = {}, []
    for r in conn.execute(
        "SELECT id, course_snapshot, player_name, played_on, created_at, updated_at "
        "FROM rounds ORDER BY created_at, id"
    ):
        snap_key = hashlib.sha256(r["course_snapshot"].encode()).hexdigest()[:16]
        snapshots[snap_key] = json.loads(r["course_snapshot"])
        holes = [
            [h["hole_number"], h["score"], h["putts"], h["gir"], h["fairway"]]
            for h in conn.execute(
                "SELECT hole_number, score, putts, gir, fairway FROM round_holes "
                "WHERE round_id = ? ORDER BY hole_number", (r["id"],))
        ]
        rounds.append({
            "id": r["id"], "player_name": r["player_name"], "played_on": r["played_on"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "snapshot": snap_key, "holes": holes,
        })
    return {"format": 1, "snapshots": snapshots, "rounds": rounds}


def import_rounds(conn, data):
    """
    Add rounds from an export that aren't already in the database. Existing
    rounds are never overwritten. Returns the number of rounds added.
    """
    if data.get("format") != 1:
        raise ValueError("Unsupported backup format")
    added = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = {r[0] for r in conn.execute("SELECT id FROM rounds")}
        for rec in data.get("rounds", []):
            if rec["id"] in existing:
                continue
            snapshot = dict(data["snapshots"][rec["snapshot"]])
            # Database ids differ between installs; re-link by slug and tee name.
            course = conn.execute(
                "SELECT id FROM courses WHERE slug = ?", (snapshot.get("course_slug"),)
            ).fetchone()
            snapshot["course_id"] = course[0] if course else None
            tee = None
            if course and snapshot.get("tee_name"):
                tee = conn.execute(
                    "SELECT id FROM tees WHERE course_id = ? AND name = ?",
                    (course[0], snapshot["tee_name"]),
                ).fetchone()
            snapshot["tee_id"] = tee[0] if tee else None
            entries = [
                {"hole_number": h[0], "score": h[1], "putts": h[2], "gir": h[3], "fairway": h[4]}
                for h in rec["holes"]
            ]
            _upsert_round(conn, rec["id"], snapshot, entries, rec.get("player_name"), rec["played_on"])
            conn.execute(
                "UPDATE rounds SET created_at = ?, updated_at = ? WHERE id = ?",
                (rec["created_at"], rec["updated_at"], rec["id"]),
            )
            added += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return added
