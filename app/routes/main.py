import datetime
import json
import logging
import math
import re
import uuid

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)

from app.backup import backup_now
from app.course import current_snapshot, get_course_info, get_hole, get_hole_par, hole_count, is_par_3
from app.courses import build_snapshot, course_info_from_snapshot, list_courses
from app.db import get_db
from app.email_utils import send_round_email
from app.rounds import (
    PAGE_SIZE, clean_player_name, get_round, player_names, query_rounds, round_filters,
    save_round, tee_names,
)
from app.security import email_allowed, record_email

log = logging.getLogger(__name__)
main = Blueprint("main", __name__)

MAX_SCORE = 20
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROUND_KEYS = ("round_id", "course", "scores", "gir", "fairway", "putts", "player_name", "played_on")


def _round_started():
    """True if user has started a round (session has scores dict)."""
    return "scores" in session


def _holes_played():
    """Sorted list of hole numbers that have a score entered."""
    scores = session.get("scores", {})
    return sorted([int(h) for h in scores.keys()]) if scores else []


def _end_round():
    """Forget the round in progress, keeping the form token and remembered name."""
    for key in ROUND_KEYS:
        session.pop(key, None)


def _parse_date(value):
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # Allow a day of slack for players ahead of the server's UTC clock.
    if day.year < 1990 or day > datetime.date.today() + datetime.timedelta(days=1):
        return None
    return day.isoformat()


# ------------------------------------------------------------------- start

@main.route("/", methods=["GET", "POST"])
def start_round():
    db = get_db()
    courses = list_courses(db)
    form = {
        "player_name": session.get("remembered_name", ""),
        "played_on": datetime.date.today().isoformat(),
        "course_tee": "",
    }
    errors = {}

    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        name = clean_player_name(form["player_name"])
        if not name:
            errors["player_name"] = "Enter the name to show on the leaderboard."
        played_on = _parse_date(form["played_on"])
        if not played_on:
            errors["played_on"] = "Choose the date you played (not in the future)."
        course_id, _, tee_id = form["course_tee"].partition(":")
        snapshot = None
        try:
            snapshot = build_snapshot(db, int(course_id), int(tee_id) if tee_id else None)
        except ValueError:
            pass
        if snapshot is None:
            errors["course_tee"] = "Choose a course and tee."
        if _round_started() and _holes_played() and request.form.get("confirm_discard") != "yes":
            errors["discard"] = (
                "You have a round in progress. Tick the box to discard it, or resume it instead."
            )

        if not errors:
            _end_round()
            session["round_id"] = uuid.uuid4().hex
            session["course"] = snapshot
            session["player_name"] = name
            session["remembered_name"] = name
            session["played_on"] = played_on
            session["scores"] = {}
            session["gir"] = {}
            session["fairway"] = {}
            session["putts"] = {}
            return redirect(url_for("main.hole", hole=1))

    in_progress = None
    if _round_started():
        info = get_course_info()
        in_progress = {
            "player_name": session.get("player_name"),
            "course": info["name"],
            "tee": info.get("tee_name"),
            "holes_played": len(_holes_played()),
            "hole_count": info["hole_count"],
        }
    return render_template(
        "start.html",
        courses=courses,
        form=form,
        errors=errors,
        in_progress=in_progress,
        known_names=player_names(db),
    ), (400 if errors else 200)


# ------------------------------------------------------------------- round

@main.route("/round")
def round_dashboard():
    if not _round_started():
        return redirect(url_for("main.start_round"))

    scores = session.get("scores", {})
    course_info = get_course_info()
    # Build list of (hole_num, score_or_none, par, yardage) for every hole
    scorecard = []
    for h in current_snapshot()["holes"]:
        score = scores.get(str(h["number"]))
        scorecard.append((h["number"], score, h["par"], h.get("yardage")))
    played = _holes_played()
    total = sum(scores.get(str(h), 0) for h in played)
    par = sum(get_hole_par(h) for h in played)

    return render_template(
        "round.html",
        scorecard=scorecard,
        course_info=course_info,
        player_name=session.get("player_name"),
        holes_played_count=len(played),
        running_total=total,
        running_to_par=total - par,
    )


@main.route("/hole/<int:hole>", methods=["GET", "POST"])
def hole(hole):
    if not _round_started():
        return redirect(url_for("main.start_round"))

    last_hole = hole_count()
    if hole < 1 or hole > last_hole:
        return redirect(url_for("main.round_dashboard"))

    hole_info = get_hole(hole)
    errors = {}
    values = {
        "score": session.get("scores", {}).get(str(hole)) or hole_info["par"],
        "putts": session.get("putts", {}).get(str(hole), 2),
        "gir": session.get("gir", {}).get(str(hole), False),
        "fairway": session.get("fairway", {}).get(str(hole)),
    }

    if request.method == "POST":
        try:
            score = int(request.form.get("score", ""))
        except ValueError:
            score = None
        try:
            putts = int(request.form.get("putts", "0") or 0)
        except ValueError:
            putts = None
        fairway = request.form.get("fairway")
        values = {
            "score": score if score is not None else request.form.get("score", ""),
            "putts": putts if putts is not None else request.form.get("putts", ""),
            "gir": request.form.get("gir") == "yes",
            "fairway": fairway if fairway in ("hit", "left", "right") else None,
        }
        if score is None or not 1 <= score <= MAX_SCORE:
            errors["score"] = f"Score must be a whole number from 1 to {MAX_SCORE}."
        if putts is None or putts < 0:
            errors["putts"] = "Putts must be 0 or more."
        elif score is not None and not errors.get("score") and putts >= score:
            errors["putts"] = "Putts must be fewer than strokes."

        if not errors:
            for key, val in (("scores", score), ("putts", putts), ("gir", values["gir"])):
                d = session.get(key, {})
                d[str(hole)] = val
                session[key] = d
            if not is_par_3(hole):
                d = session.get("fairway", {})
                d[str(hole)] = values["fairway"]
                session["fairway"] = d

            action = request.form.get("action", "next")
            if action == "prev" and hole > 1:
                return redirect(url_for("main.hole", hole=hole - 1))
            if action == "card":
                return redirect(url_for("main.round_dashboard"))
            if action == "next" and hole < last_hole:
                return redirect(url_for("main.hole", hole=hole + 1))
            return redirect(url_for("main.finish"))

    return render_template(
        "hole.html",
        hole=hole,
        last_hole=last_hole,
        par=hole_info["par"],
        yardage=hole_info.get("yardage"),
        handicap=hole_info.get("handicap"),
        course_info=get_course_info(),
        is_par3=is_par_3(hole),
        values=values,
        errors=errors,
        scored=str(hole) in session.get("scores", {}),
        holes_played_count=len(_holes_played()),
    ), (400 if errors else 200)


# ------------------------------------------------------------------ finish

def _round_summary():
    """Template context for the finish page, computed from the session."""
    holes_played = _holes_played()
    scores_dict = session.get("scores", {})
    course_info = get_course_info()
    scores_list = [scores_dict.get(str(h), 0) for h in holes_played]
    total = sum(scores_list)
    total_par = sum(get_hole_par(h) for h in holes_played)
    return dict(
        total=total,
        total_par=total_par,
        to_par=total - total_par,
        num_holes=len(holes_played),
        course_info=course_info,
        player_name=session.get("player_name"),
        played_on=session.get("played_on"),
        missing=[h for h in range(1, course_info["hole_count"] + 1) if h not in holes_played],
    )


@main.route("/finish", methods=["GET", "POST"])
def finish():
    if not _round_started():
        return redirect(url_for("main.start_round"))
    if not _holes_played():
        return redirect(url_for("main.round_dashboard"))

    if request.method == "POST":
        # Rounds started before player names existed need one now.
        if not session.get("player_name"):
            name = clean_player_name(request.form.get("player_name", ""))
            if not name:
                return render_template(
                    "finish.html", name_error="Enter the name to show on the leaderboard.",
                    **_round_summary()), 400
            session["player_name"] = name
            session["remembered_name"] = name

        round_id = session.setdefault("round_id", uuid.uuid4().hex)
        entries = [
            {
                "hole_number": h,
                "score": session["scores"][str(h)],
                "putts": session.get("putts", {}).get(str(h)),
                "gir": session.get("gir", {}).get(str(h)),
                "fairway": session.get("fairway", {}).get(str(h)),
            }
            for h in _holes_played()
        ]
        db = get_db()
        save_round(
            db, round_id, current_snapshot(), entries,
            session["player_name"], session.get("played_on") or datetime.date.today().isoformat(),
        )
        backup_now(db)
        _end_round()
        # Emailing is offered only for the round just saved, from this browser.
        session["emailable_round"] = round_id
        flash("Round saved.")
        return redirect(url_for("main.round_detail", round_id=round_id))

    return render_template("finish.html", **_round_summary())


@main.route("/rounds/<round_id>/email", methods=["POST"])
def email_round(round_id):
    if session.get("emailable_round") != round_id:
        abort(403)
    db = get_db()
    rnd = get_round(db, round_id)
    if rnd is None:
        abort(404)

    email = request.form.get("email", "").strip()
    if len(email) > 254 or not EMAIL_RE.match(email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("main.round_detail", round_id=round_id))

    allowed, reason = email_allowed(round_id)
    if not allowed:
        flash(reason, "error")
        return redirect(url_for("main.round_detail", round_id=round_id))

    holes_played = sorted(rnd["holes"])
    h = rnd["holes"]
    status, message = send_round_email(
        rnd["player_name"] or "Golfer",
        email,
        holes_played,
        [h[n]["score"] for n in holes_played],
        [bool(h[n]["gir"]) for n in holes_played],
        [h[n]["fairway"] for n in holes_played],
        [h[n]["putts"] or 0 for n in holes_played],
        course_info_from_snapshot(rnd["snapshot"]),
    )
    if status == 200:
        record_email(round_id)
        flash("Summary sent. Check your inbox.")
    else:
        log.error("Round email failed: %s %s", status, message)
        flash("The email couldn't be sent right now. Your round is saved.", "error")
    return redirect(url_for("main.round_detail", round_id=round_id))


# ------------------------------------------------------------------ rounds

@main.route("/rounds")
def rounds_list():
    db = get_db()
    f = round_filters(request.args)
    rows, total = query_rounds(db, f)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    if f["page"] > pages:
        f["page"] = pages
        rows, total = query_rounds(db, f)

    def page_url(**changes):
        args = {k: v for k, v in request.args.items() if v}
        args.update({k: v for k, v in changes.items()})
        args = {k: v for k, v in args.items() if v not in ("", None)}
        return url_for("main.rounds_list", **args)

    return render_template(
        "rounds.html",
        rounds=rows,
        total=total,
        filters=f,
        pages=pages,
        page_url=page_url,
        offset=(f["page"] - 1) * PAGE_SIZE,
        courses=list_courses(db),
        tees=tee_names(db),
        known_names=player_names(db),
    )


@main.route("/rounds/<round_id>")
def round_detail(round_id):
    if not re.fullmatch(r"[0-9a-f]{32}", round_id):
        abort(404)
    rnd = get_round(get_db(), round_id)
    if rnd is None:
        abort(404)
    return render_template(
        "round_detail.html",
        rnd=rnd,
        can_email=session.get("emailable_round") == round_id,
    )


# ----------------------------------------------------------------- courses

@main.route("/courses")
def courses_list():
    return render_template("courses.html", courses=list_courses(get_db()))


@main.route("/courses/<slug>")
def course_detail(slug):
    db = get_db()
    course = db.execute("SELECT * FROM courses WHERE slug = ?", (slug,)).fetchone()
    if course is None:
        abort(404)
    tees = db.execute(
        "SELECT * FROM tees WHERE course_id = ? ORDER BY published_yardage DESC, name", (course["id"],)
    ).fetchall()
    holes = db.execute(
        "SELECT hole_number, par, handicap FROM course_holes WHERE course_id = ? ORDER BY hole_number",
        (course["id"],),
    ).fetchall()
    yardages = {}
    for t in tees:
        yardages[t["id"]] = {
            r["hole_number"]: r["yardage"]
            for r in db.execute("SELECT hole_number, yardage FROM tee_holes WHERE tee_id = ?", (t["id"],))
        }
    def _sum(ys, numbers):
        values = [ys.get(n) for n in numbers]
        return sum(values) if values and all(values) else None

    try:
        selected_tee = int(request.args.get("tee", ""))
    except ValueError:
        selected_tee = None
    if selected_tee not in yardages:
        selected_tee = tees[0]["id"] if tees else None

    count = len(holes)
    totals = {
        tee_id: {"out": _sum(ys, range(1, 10)), "in": _sum(ys, range(10, 19)), "total": _sum(ys, range(1, count + 1))}
        for tee_id, ys in yardages.items()
    }
    return render_template(
        "course_detail.html", course=course, tees=tees, holes=holes, yardages=yardages, totals=totals, selected_tee=selected_tee,
        provenance=json.loads(course["provenance"] or "{}"),
    )


@main.app_errorhandler(400)
@main.app_errorhandler(403)
@main.app_errorhandler(404)
def http_error(e):
    return render_template("error.html", error=e), e.code
