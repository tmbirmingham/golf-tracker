"""
Course reference data: validation, import from JSON, and round snapshots.

Course files live in app/data/courses/*.json. Every field that comes from a
source carries a provenance status:
  verified      - confirmed against the course's official source
  unverified    - from a secondary source, not yet confirmed
  user_entered  - entered manually by the app's user
"""
import glob
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses")

PROVENANCE_STATUSES = ("verified", "unverified", "user_entered")
COURSE_FIELDS = (
    "name", "address", "city", "state", "phone", "website",
    "opened_year", "description", "published_par", "data_version",
)


class CourseDataError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


# ---------------------------------------------------------------- validation

def validate_course_data(data):
    """Return a list of problems with a course definition (empty if valid)."""
    errors = []
    if not data.get("slug"):
        errors.append("slug is required")
    if not data.get("name"):
        errors.append("name is required")
    version = data.get("data_version", 1)
    if not isinstance(version, int) or version < 1:
        errors.append("data_version must be a positive integer")

    for field, info in (data.get("provenance") or {}).items():
        if info.get("status") not in PROVENANCE_STATUSES:
            errors.append(f"provenance.{field}: status must be one of {PROVENANCE_STATUSES}")

    holes = data.get("holes") or []
    numbers = [h.get("number") for h in holes]
    if len(holes) not in (9, 18):
        errors.append(f"expected 9 or 18 holes, got {len(holes)}")
    if sorted(n for n in numbers if isinstance(n, int)) != list(range(1, len(holes) + 1)):
        errors.append("hole numbers must be 1..N with no gaps or duplicates")
    pars = {}
    for h in holes:
        par = h.get("par")
        if not isinstance(par, int) or not 3 <= par <= 6:
            errors.append(f"hole {h.get('number')}: par must be an integer 3-6")
        else:
            pars[h.get("number")] = par
        hcp = h.get("handicap")
        if hcp is not None and (not isinstance(hcp, int) or not 1 <= hcp <= 18):
            errors.append(f"hole {h.get('number')}: handicap must be 1-18")
    handicaps = [h["handicap"] for h in holes if h.get("handicap") is not None]
    if len(handicaps) != len(set(handicaps)):
        errors.append("hole handicaps must be unique")

    published_par = data.get("published_par")
    if published_par is not None and len(pars) == len(holes) and sum(pars.values()) != published_par:
        errors.append(f"hole pars sum to {sum(pars.values())} but published par is {published_par}")

    tee_names = set()
    for tee in data.get("tees") or []:
        errors.extend(_validate_tee(tee, pars, len(holes)))
        if tee.get("name") in tee_names:
            errors.append(f"duplicate tee name {tee.get('name')!r}")
        tee_names.add(tee.get("name"))
    return errors


def _validate_tee(tee, course_pars, hole_count):
    errors = []
    name = tee.get("name")
    label = f"tee {name!r}"
    if not name:
        errors.append("tee name is required")
    if tee.get("status") not in PROVENANCE_STATUSES:
        errors.append(f"{label}: status must be one of {PROVENANCE_STATUSES}")
    rating = tee.get("course_rating")
    if rating is not None and not 50 <= rating <= 85:
        errors.append(f"{label}: course_rating {rating} out of range")
    slope = tee.get("slope_rating")
    if slope is not None and not 55 <= slope <= 155:
        errors.append(f"{label}: slope_rating {slope} out of range")

    tee_holes = {h.get("number"): h for h in tee.get("holes") or []}
    if tee_holes and sorted(tee_holes) != list(range(1, hole_count + 1)):
        errors.append(f"{label}: hole entries must cover holes 1..{hole_count}")
        return errors

    yardages = [h.get("yardage") for h in tee_holes.values()]
    if any(y is not None and (not isinstance(y, int) or y <= 0) for y in yardages):
        errors.append(f"{label}: yardages must be positive integers")
    elif tee_holes and all(y is not None for y in yardages):
        total = sum(yardages)
        published = tee.get("published_yardage")
        if published is not None and total != published:
            errors.append(f"{label}: hole yardages sum to {total} but published total is {published}")

    if tee.get("published_par") is not None and course_pars:
        tee_par = sum(
            (tee_holes.get(n) or {}).get("par") or course_pars.get(n, 0)
            for n in range(1, hole_count + 1)
        )
        if tee_par != tee["published_par"]:
            errors.append(f"{label}: hole pars sum to {tee_par} but published par is {tee['published_par']}")
    return errors


# -------------------------------------------------------------------- import

def load_course_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def import_course(conn, data, update=False):
    """
    Insert a course definition. Returns "created", "updated" or "skipped".

    Without update, an existing course (matched by slug) is left untouched.
    With update, course fields, holes and tees from the file are upserted.
    Nothing is ever deleted, and rounds are never modified: they keep their
    own snapshot of the course as played.
    """
    errors = validate_course_data(data)
    if errors:
        raise CourseDataError(errors)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM courses WHERE slug = ?", (data["slug"],)).fetchone()
        if row and not update:
            conn.rollback()
            return "skipped"

        values = [data.get(f) for f in COURSE_FIELDS]
        values[COURSE_FIELDS.index("data_version")] = data.get("data_version", 1)
        provenance = json.dumps(data.get("provenance") or {}, sort_keys=True)
        if row:
            course_id = row["id"]
            conn.execute(
                "UPDATE courses SET " + ", ".join(f"{f} = ?" for f in COURSE_FIELDS)
                + ", provenance = ?, updated_at = datetime('now') WHERE id = ?",
                values + [provenance, course_id],
            )
        else:
            cur = conn.execute(
                "INSERT INTO courses (slug, " + ", ".join(COURSE_FIELDS) + ", provenance) VALUES ("
                + ", ".join("?" * (len(COURSE_FIELDS) + 2)) + ")",
                [data["slug"]] + values + [provenance],
            )
            course_id = cur.lastrowid

        for h in data["holes"]:
            conn.execute(
                "INSERT INTO course_holes (course_id, hole_number, par, handicap) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (course_id, hole_number) DO UPDATE SET par = excluded.par, handicap = excluded.handicap",
                (course_id, h["number"], h["par"], h.get("handicap")),
            )

        for tee in data.get("tees") or []:
            conn.execute(
                "INSERT INTO tees (course_id, name, color, gender, course_rating, slope_rating, "
                "published_par, published_yardage, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (course_id, name) DO UPDATE SET color = excluded.color, gender = excluded.gender, "
                "course_rating = excluded.course_rating, slope_rating = excluded.slope_rating, "
                "published_par = excluded.published_par, published_yardage = excluded.published_yardage, "
                "status = excluded.status, source = excluded.source",
                (course_id, tee["name"], tee.get("color"), tee.get("gender"), tee.get("course_rating"),
                 tee.get("slope_rating"), tee.get("published_par"), tee.get("published_yardage"),
                 tee["status"], tee.get("source")),
            )
            tee_id = conn.execute(
                "SELECT id FROM tees WHERE course_id = ? AND name = ?", (course_id, tee["name"])
            ).fetchone()["id"]
            for th in tee.get("holes") or []:
                conn.execute(
                    "INSERT INTO tee_holes (tee_id, hole_number, yardage, par, handicap) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (tee_id, hole_number) DO UPDATE SET yardage = excluded.yardage, "
                    "par = excluded.par, handicap = excluded.handicap",
                    (tee_id, th["number"], th.get("yardage"), th.get("par"), th.get("handicap")),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return "updated" if row else "created"


def bundled_course_files():
    return sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))


def seed_bundled_courses(conn):
    """
    Import bundled course files that aren't in the database yet, and re-apply
    a file whose data_version is higher than the version stored. Bumping
    data_version in a committed file is the deliberate way to roll out
    corrected course data on deploy.
    """
    for path in bundled_course_files():
        data = load_course_file(path)
        row = conn.execute(
            "SELECT data_version FROM courses WHERE slug = ?", (data.get("slug"),)
        ).fetchone()
        if row is None:
            import_course(conn, data, update=False)
        elif data.get("data_version", 1) > row["data_version"]:
            import_course(conn, data, update=True)


# -------------------------------------------------------------------- queries

def list_courses(conn):
    """Courses with their tees, for the start-round picker."""
    courses = []
    for c in conn.execute("SELECT id, slug, name, city, state FROM courses ORDER BY name"):
        tees = conn.execute(
            "SELECT id, name, color, published_yardage, course_rating, slope_rating, status "
            "FROM tees WHERE course_id = ? ORDER BY published_yardage DESC, name",
            (c["id"],),
        ).fetchall()
        courses.append({**dict(c), "tees": [dict(t) for t in tees]})
    return courses


def build_snapshot(conn, course_id, tee_id=None):
    """
    Freeze the course (and optional tee) as it is right now, for use during a
    round and for storing alongside the saved round. Returns None if the
    course or tee doesn't exist.
    """
    course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    if course is None:
        return None
    provenance = json.loads(course["provenance"] or "{}")

    tee = None
    tee_holes = {}
    if tee_id is not None:
        tee = conn.execute(
            "SELECT * FROM tees WHERE id = ? AND course_id = ?", (tee_id, course_id)
        ).fetchone()
        if tee is None:
            return None
        tee_holes = {
            r["hole_number"]: r
            for r in conn.execute("SELECT * FROM tee_holes WHERE tee_id = ?", (tee_id,))
        }

    holes = []
    for h in conn.execute(
        "SELECT hole_number, par, handicap FROM course_holes WHERE course_id = ? ORDER BY hole_number",
        (course_id,),
    ):
        th = tee_holes.get(h["hole_number"])
        holes.append({
            "number": h["hole_number"],
            "par": (th["par"] if th and th["par"] else h["par"]),
            "handicap": (th["handicap"] if th and th["handicap"] else h["handicap"]),
            "yardage": th["yardage"] if th else None,
        })

    yardages = [h["yardage"] for h in holes]
    return {
        "course_id": course["id"],
        "course_slug": course["slug"],
        "course_name": course["name"],
        "par_status": (provenance.get("holes.par") or {}).get("status", "unverified"),
        "tee_id": tee["id"] if tee else None,
        "tee_name": tee["name"] if tee else None,
        "tee_status": tee["status"] if tee else None,
        "course_rating": tee["course_rating"] if tee else None,
        "slope_rating": tee["slope_rating"] if tee else None,
        "total_par": sum(h["par"] for h in holes),
        "total_yardage": sum(yardages) if holes and all(yardages) else None,
        "holes": holes,
    }


def course_info_from_snapshot(snapshot):
    """Shape used by templates and email: hole-number keyed lookups."""
    return {
        "name": snapshot["course_name"],
        "tee_name": snapshot.get("tee_name"),
        "tee_status": snapshot.get("tee_status"),
        "par_status": snapshot.get("par_status"),
        "course_rating": snapshot.get("course_rating"),
        "slope_rating": snapshot.get("slope_rating"),
        "total_par": snapshot["total_par"],
        "total_yardage": snapshot.get("total_yardage"),
        "hole_count": len(snapshot["holes"]),
        "holes": {h["number"]: h["par"] for h in snapshot["holes"]},
        "yardages": {h["number"]: h.get("yardage") for h in snapshot["holes"]},
        "handicaps": {h["number"]: h.get("handicap") for h in snapshot["holes"]},
    }
