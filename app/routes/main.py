import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.email_utils import send_round_email, send_test_email
from app.course import get_hole, get_hole_par, is_par_3, get_course_info, hole_count, current_snapshot
from app.courses import build_snapshot, list_courses
from app.db import get_db
from app.rounds import save_round, list_rounds

main = Blueprint("main", __name__)


def _round_started():
    """True if user has started a round (session has scores dict)."""
    return "scores" in session


def _holes_played():
    """Sorted list of hole numbers that have a score entered."""
    scores = session.get("scores", {})
    return sorted([int(h) for h in scores.keys()]) if scores else []


@main.route("/", methods=["GET", "POST"])
def start_round():
    db = get_db()
    courses = list_courses(db)

    if request.method == "POST":
        # Value is "<course_id>" or "<course_id>:<tee_id>"
        course_id, _, tee_id = request.form.get("course_tee", "").partition(":")
        snapshot = None
        try:
            snapshot = build_snapshot(db, int(course_id), int(tee_id) if tee_id else None)
        except ValueError:
            pass
        if snapshot is None:
            return render_template("start.html", courses=courses, error="Please choose a course and tee."), 400

        session.clear()
        session["round_id"] = uuid.uuid4().hex
        session["course"] = snapshot
        session["scores"] = {}
        session["gir"] = {}
        session["fairway"] = {}
        session["putts"] = {}
        return redirect(url_for("main.round_dashboard"))

    return render_template("start.html", courses=courses)


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
    holes_played_count = len(_holes_played())

    return render_template(
        "round.html",
        scorecard=scorecard,
        course_info=course_info,
        holes_played_count=holes_played_count,
    )


@main.route("/hole/<int:hole>", methods=["GET", "POST"])
def hole(hole):
    if not _round_started():
        return redirect(url_for("main.start_round"))

    last_hole = hole_count()
    if hole < 1 or hole > last_hole:
        return redirect(url_for("main.round_dashboard"))

    if request.method == "POST":
        try:
            score = int(request.form.get("score", 0))
        except (TypeError, ValueError):
            score = get_hole_par(hole)
        if score < 1:
            score = 1
        scores = session.get("scores", {})
        scores[str(hole)] = score
        session["scores"] = scores

        gir = session.get("gir", {})
        gir[str(hole)] = request.form.get("gir") == "yes"
        session["gir"] = gir

        if not is_par_3(hole):
            fairway = session.get("fairway", {})
            fairway_status = request.form.get("fairway", "none")
            if fairway_status in ["hit", "left", "right"]:
                fairway[str(hole)] = fairway_status
            else:
                fairway[str(hole)] = None
            session["fairway"] = fairway

        putts = session.get("putts", {})
        try:
            putts_val = int(request.form.get("putts", 0))
            putts[str(hole)] = max(0, min(putts_val, 10))
        except (TypeError, ValueError):
            putts[str(hole)] = 0
        session["putts"] = putts

        # Redirect: "save_and_next" goes to next hole, else back to round
        if request.form.get("action") == "save_and_next" and hole < last_hole:
            return redirect(url_for("main.hole", hole=hole + 1))
        return redirect(url_for("main.round_dashboard"))

    hole_info = get_hole(hole)
    return render_template(
        "hole.html",
        hole=hole,
        last_hole=last_hole,
        par=hole_info["par"],
        yardage=hole_info.get("yardage"),
        handicap=hole_info.get("handicap"),
        course_info=get_course_info(),
        is_par3=is_par_3(hole),
        current_gir=session.get("gir", {}).get(str(hole), False),
        current_fairway=session.get("fairway", {}).get(str(hole)),
        current_putts=session.get("putts", {}).get(str(hole), 0),
        current_score=session.get("scores", {}).get(str(hole)),
    )


def _round_summary():
    """Template context for the finish page, computed from the session."""
    holes_played = _holes_played()
    scores_dict = session.get("scores", {})
    gir_dict = session.get("gir", {})
    fairway_dict = session.get("fairway", {})
    putts_dict = session.get("putts", {})

    scores_list = [scores_dict.get(str(h), 0) for h in holes_played]
    gir_list = [gir_dict.get(str(h), False) for h in holes_played]
    fairway_list = [fairway_dict.get(str(h)) for h in holes_played]
    putts_list = [putts_dict.get(str(h), 0) for h in holes_played]

    course_info = get_course_info()
    total = sum(scores_list)
    total_par = sum(get_hole_par(h) for h in holes_played)
    num_holes = len(holes_played)
    fairway_hit = sum(1 for f in fairway_list if f == "hit")
    fairway_left = sum(1 for f in fairway_list if f == "left")
    fairway_right = sum(1 for f in fairway_list if f == "right")
    total_putts = sum(putts_list)

    return dict(
        total=total,
        holes_played=holes_played,
        scores_list=scores_list,
        pars_list=[course_info["holes"].get(h) for h in holes_played],
        yardages_list=[course_info["yardages"].get(h) for h in holes_played],
        gir_list=gir_list,
        fairway_list=fairway_list,
        putts_list=putts_list,
        total_putts=total_putts,
        putts_avg=round(total_putts / num_holes, 1) if num_holes else 0,
        gir_count=sum(gir_list),
        fairway_hit=fairway_hit,
        fairway_left=fairway_left,
        fairway_right=fairway_right,
        fairway_total=fairway_hit + fairway_left + fairway_right,
        total_par=total_par,
        to_par=total - total_par,
        course_info=course_info,
        num_holes=num_holes,
    )


def _save_current_round():
    """Persist the round in progress. Only writes to rounds tables, never course data."""
    if "round_id" not in session:
        session["round_id"] = uuid.uuid4().hex
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
    save_round(get_db(), session["round_id"], current_snapshot(), entries)


@main.route("/finish", methods=["GET", "POST"])
def finish():
    if not _round_started():
        return redirect(url_for("main.start_round"))

    if not _holes_played():
        return redirect(url_for("main.round_dashboard"))

    summary = _round_summary()

    if request.method == "POST":
        _save_current_round()

        if request.form.get("action") == "save_only":
            session.clear()
            return render_template("finish.html", saved=True, **summary)

        email = request.form.get("email", "").strip()
        if not email:
            return render_template("finish.html", error="Please enter your email address.", **summary)

        status, message = send_round_email(
            "Golfer",
            email,
            summary["holes_played"],
            summary["scores_list"],
            summary["gir_list"],
            summary["fairway_list"],
            summary["putts_list"],
            summary["course_info"],
        )

        if status == 200:
            session.clear()
            return render_template("finish.html", sent=True, saved=True, to_email=email, **summary)
        return render_template("finish.html", error=f"Could not send email: {message}", **summary)

    return render_template("finish.html", **summary)


@main.route("/rounds")
def rounds_history():
    return render_template("rounds.html", rounds=list_rounds(get_db()))


@main.route("/test-email")
def test_email():
    status, response = send_test_email(os.getenv("MAIL_FROM_EMAIL"))
    return f"Status: {status}<br>{response}"
