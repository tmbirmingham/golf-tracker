import os
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.email_utils import send_round_email, send_test_email
from app.course import get_hole_par, is_par_3, get_course_info

main = Blueprint("main", __name__)


@main.route("/", methods=["GET", "POST"])
def start_round():
    if request.method == "POST":
        session.clear()
        session["current_hole"] = 1
        session["scores"] = {}
        session["gir"] = {}  # Green in regulation: {hole: True/False}
        session["fairway"] = {}  # Fairway: {hole: "hit"/"left"/"right"/None}
        session["putts"] = {}  # Putts per hole: {hole: int}
        return redirect(url_for("main.hole", hole=1))

    course_info = get_course_info()
    return render_template("start.html", course_info=course_info)


@main.route("/hole/<int:hole>", methods=["GET", "POST"])
def hole(hole):
    if "current_hole" not in session:
        return redirect(url_for("main.start_round"))

    if hole < 1 or hole > 18:
        return redirect(url_for("main.hole", hole=session["current_hole"]))

    # Only allow entering score for the current hole or previous holes (no skipping ahead)
    if hole > session["current_hole"]:
        return redirect(url_for("main.hole", hole=session["current_hole"]))

    if request.method == "POST":
        score = int(request.form.get("score", 0))
        if score < 1:
            score = 1
        scores = session.get("scores", {})
        scores[str(hole)] = score
        session["scores"] = scores

        # Save GIR (Green in Regulation)
        gir = session.get("gir", {})
        gir[str(hole)] = request.form.get("gir") == "yes"
        session["gir"] = gir

        # Save fairway stats (only for par 4s and 5s)
        if not is_par_3(hole):
            fairway = session.get("fairway", {})
            fairway_status = request.form.get("fairway", "none")
            if fairway_status in ["hit", "left", "right"]:
                fairway[str(hole)] = fairway_status
            else:
                fairway[str(hole)] = None
            session["fairway"] = fairway

        # Save putts
        putts = session.get("putts", {})
        try:
            putts_val = int(request.form.get("putts", 0))
            putts[str(hole)] = max(0, min(putts_val, 10))
        except (TypeError, ValueError):
            putts[str(hole)] = 0
        session["putts"] = putts

        if hole == 18:
            return redirect(url_for("main.finish"))

        if request.form.get("action") == "finish":
            return redirect(url_for("main.finish"))

        if request.form.get("action") == "back":
            return redirect(url_for("main.hole", hole=max(1, hole - 1)))

        session["current_hole"] = max(session.get("current_hole", 1), hole + 1)
        return redirect(url_for("main.hole", hole=hole + 1))

    par = get_hole_par(hole)
    is_par3 = is_par_3(hole)
    current_score = session.get("scores", {}).get(str(hole))
    current_gir = session.get("gir", {}).get(str(hole), False)
    current_fairway = session.get("fairway", {}).get(str(hole))
    current_putts = session.get("putts", {}).get(str(hole), 0)

    return render_template(
        "hole.html",
        hole=hole,
        par=par,
        current_score=current_score,
        is_par3=is_par3,
        current_gir=current_gir,
        current_fairway=current_fairway,
        current_putts=current_putts,
    )


@main.route("/finish", methods=["GET", "POST"])
def finish():
    scores_dict = session.get("scores", {})
    if not scores_dict:
        return redirect(url_for("main.start_round"))

    # Only include holes that have scores
    played_holes = sorted([int(h) for h in scores_dict.keys()])
    num_holes_played = len(played_holes)

    scores_list = [scores_dict.get(str(i), 0) for i in played_holes]
    total = sum(scores_list)

    # Get stats
    gir_dict = session.get("gir", {})
    gir_list = [gir_dict.get(str(i), False) for i in played_holes]
    fairway_dict = session.get("fairway", {})
    fairway_list = [fairway_dict.get(str(i)) for i in played_holes]
    putts_dict = session.get("putts", {})
    putts_list = [putts_dict.get(str(i), 0) for i in played_holes]

    # Calculate stats
    gir_count = sum(gir_list)
    fairway_hit = sum(1 for f in fairway_list if f == "hit")
    fairway_left = sum(1 for f in fairway_list if f == "left")
    fairway_right = sum(1 for f in fairway_list if f == "right")
    fairway_total = fairway_hit + fairway_left + fairway_right
    total_putts = sum(putts_list)
    putts_avg = round(total_putts / num_holes_played, 1) if num_holes_played > 0 else 0

    course_info = get_course_info()
    # Calculate total par for only played holes
    total_par = sum(course_info["holes"].get(h, 4) for h in played_holes)
    to_par = total - total_par

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            return render_template(
                "finish.html",
                total=total,
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
                error="Please enter your email address.",
            )

        try:
            status, message = send_round_email(
                "Golfer",
                email,
                scores_list,
                gir_list,
                fairway_list,
                putts_list,
                course_info,
                played_holes,
            )
        except Exception as e:
            import traceback
            print(f"ERROR in finish route: {e}")
            traceback.print_exc()
            status, message = 500, str(e)
        session.clear()

        if status == 200:
            return render_template(
                "finish.html",
                total=total,
                total_par=total_par,
                to_par=to_par,
                sent=True,
                email=email,
            )
        return render_template(
            "finish.html",
            total=total,
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
            error=f"Could not send email: {message}",
        )

    return render_template(
        "finish.html",
        total=total,
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
    )


@main.route("/test-email")
def test_email():
    status, response = send_test_email(os.getenv("MAIL_FROM_EMAIL"))
    return f"Status: {status}<br>{response}"
