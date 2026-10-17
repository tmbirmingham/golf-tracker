"""
The player's saved rounds. Rounds store their own copy of the course/tee
details as played and never write to the course tables.
"""
import json


def save_round(conn, round_id, snapshot, entries):
    """
    Insert or replace a round. entries: list of dicts with hole_number, score,
    putts, gir, fairway. Re-saving the same round_id replaces its hole scores.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO rounds (id, course_id, tee_id, course_name, tee_name, course_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET updated_at = datetime('now')",
            (round_id, snapshot["course_id"], snapshot.get("tee_id"), snapshot["course_name"],
             snapshot.get("tee_name"), json.dumps(snapshot, sort_keys=True)),
        )
        conn.execute("DELETE FROM round_holes WHERE round_id = ?", (round_id,))
        conn.executemany(
            "INSERT INTO round_holes (round_id, hole_number, score, putts, gir, fairway) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(round_id, e["hole_number"], e["score"], e.get("putts"),
              None if e.get("gir") is None else int(bool(e["gir"])), e.get("fairway"))
             for e in entries],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_rounds(conn, limit=50):
    """Saved rounds, newest first, with totals computed from the round's own snapshot."""
    results = []
    for r in conn.execute(
        "SELECT id, course_name, tee_name, played_on, course_snapshot FROM rounds "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ):
        pars = {h["number"]: h["par"] for h in json.loads(r["course_snapshot"])["holes"]}
        holes = conn.execute(
            "SELECT hole_number, score FROM round_holes WHERE round_id = ?", (r["id"],)
        ).fetchall()
        total = sum(h["score"] for h in holes)
        par = sum(pars.get(h["hole_number"], 0) for h in holes)
        results.append({
            "id": r["id"],
            "course_name": r["course_name"],
            "tee_name": r["tee_name"],
            "played_on": r["played_on"],
            "holes_played": len(holes),
            "total": total,
            "to_par": total - par,
        })
    return results
