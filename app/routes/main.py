import os
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.email_utils import send_round_email, send_test_email
from app.course import get_hole_par, is_par_3, get_course_info

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
    if request.method == "POST":
        session.clear()
        session["scores"] = {}
        session["gir"] = {}
        session["fairway"] = {}
        session["putts"] = {}
        return redirect(url_for("main.round_dashboard"))

    course_info = get_course_info()
    return render_template("start.html", course_info=course_info)


@main.route("/round")
def round_dashboard():
    if not _round_started():
        return redirect(url_for("main.start_round"))

    scores = session.get("scores", {})
    course_info = get_course_info()
    # Build list of (hole_num, score_or_none, par) for 1-18
    scorecard = []
    for h in range(1, 19):
        score = scores.get(str(h))
        scorecard.append((h, score, get_hole_par(h)))
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

    if hole < 1 or hole > 18:
        return redirect(url_for("main.round_dashboard"))

    if request.method == "POST":
        score = int(request.form.get("score", 0))
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
        if request.form.get("action") == "save_and_next" and hole < 18:
            return redirect(url_for("main.hole", hole=hole + 1))
        return redirect(url_for("main.round_dashboard"))

    par = get_hole_par(hole)
    is_par3 = is_par_3(hole)
    current_gir = session.get("gir", {}).get(str(hole), False)
    current_fairway = session.get("fairway", {}).get(str(hole))
    current_putts = session.get("putts", {}).get(str(hole), 0)
    current_score = session.get("scores", {}).get(str(hole))

    return render_template(
        "hole.html",
        hole=hole,
        par=par,
        is_par3=is_par3,
        current_gir=current_gir,
        current_fairway=current_fairway,
        current_putts=current_putts,
        current_score=current_score,
    )


@main.route("/finish", methods=["GET", "POST"])
def finish():
    if not _round_started():
        return redirect(url_for("main.start_round"))

    holes_played = _holes_played()
    if not holes_played:
        return redirect(url_for("main.round_dashboard"))

    scores_dict = session.get("scores", {})
    gir_dict = session.get("gir", {})
    fairway_dict = session.get("fairway", {})
    putts_dict = session.get("putts", {})

    scores_list = [scores_dict.get(str(h), 0) for h in holes_played]
    gir_list = [gir_dict.get(str(h), False) for h in holes_played]
    fairway_list = [fairway_dict.get(str(h)) for h in holes_played]
    putts_list = [putts_dict.get(str(h), 0) for h in holes_played]

    total = sum(scores_list)
    course_info = get_course_info()
    total_par = sum(get_hole_par(h) for h in holes_played)
    to_par = total - total_par

    num_holes = len(holes_played)
    gir_count = sum(gir_list)
    fairway_hit = sum(1 for f in fairway_list if f == "hit")
    fairway_left = sum(1 for f in fairway_list if f == "left")
    fairway_right = sum(1 for f in fairway_list if f == "right")
    fairway_total = fairway_hit + fairway_left + fairway_right
    total_putts = sum(putts_list)
    putts_avg = round(total_putts / num_holes, 1) if num_holes else 0

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            return render_template(
                "finish.html",
                total=total,
                holes_played=holes_played,
                scores_list=scores_list,
                gir_list=gir_list,
                fairway_list=fairway_list,
                putts_list=putts_list,
                total_putts=total_putts,
                putts_avg=putts_avg,
                gir_count=gir_count,
                fairway_hit=fairway_hit,
                fairway_left=fairway_left,
                fairway_right=fairway_right,
                fairway_total=fairway_total,
                total_par=total_par,
                to_par=to_par,
                course_info=course_info,
                num_holes=num_holes,
                error="Please enter your email address.",
            )

        status, message = send_round_email(
            "Golfer",
            email,
            holes_played,
            scores_list,
            gir_list,
            fairway_list,
            putts_list,
            course_info,
        )
        session.clear()

        if status == 200:
            return render_template(
                "finish.html",
                total=total,
                total_par=total_par,
                to_par=to_par,
                num_holes=num_holes,
                sent=True,
            )
        return render_template(
            "finish.html",
            total=total,
            holes_played=holes_played,
            scores_list=scores_list,
            gir_list=gir_list,
            fairway_list=fairway_list,
            putts_list=putts_list,
            total_putts=total_putts,
            putts_avg=putts_avg,
            gir_count=gir_count,
            fairway_hit=fairway_hit,
            fairway_left=fairway_left,
            fairway_right=fairway_right,
            fairway_total=fairway_total,
            total_par=total_par,
            to_par=to_par,
            course_info=course_info,
            num_holes=num_holes,
            error=f"Could not send email: {message}",
        )

    return render_template(
        "finish.html",
        total=total,
        holes_played=holes_played,
        scores_list=scores_list,
        gir_list=gir_list,
        fairway_list=fairway_list,
        putts_list=putts_list,
        total_putts=total_putts,
        putts_avg=putts_avg,
        gir_count=gir_count,
        fairway_hit=fairway_hit,
        fairway_left=fairway_left,
        fairway_right=fairway_right,
        fairway_total=fairway_total,
        total_par=total_par,
        to_par=to_par,
        course_info=course_info,
        num_holes=num_holes,
    )


@main.route("/test-email")
def test_email():
    status, response = send_test_email(os.getenv("MAIL_FROM_EMAIL"))
    return f"Status: {status}<br>{response}"
