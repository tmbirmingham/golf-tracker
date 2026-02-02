import smtplib
from email.mime.text import MIMEText
from flask import current_app


def _build_insights(holes_played, scores, putts_list, gir_list, fairway_list, course_info):
    """Build a list of insight strings from round data (variable number of holes)."""
    insights = []

    if not course_info or not scores or not holes_played:
        return insights

    holes = course_info.get("holes", {})
    num_holes = len(holes_played)
    total_par = sum(holes.get(h, 4) for h in holes_played)
    total_score = sum(scores)

    # Score vs par per hole (hole_num, score - par)
    scores_vs_par = []
    for i, hole_num in enumerate(holes_played):
        par = holes.get(hole_num, 4)
        score = scores[i] if i < len(scores) else 0
        scores_vs_par.append((hole_num, score - par))

    # Best and worst holes
    if scores_vs_par:
        best = min(scores_vs_par, key=lambda x: x[1])
        worst = max(scores_vs_par, key=lambda x: x[1])
        if best[1] < 0:
            insights.append(f"Best hole: #{best[0]} ({best[1]} vs par)")
        if worst[1] > 0:
            insights.append(f"Toughest hole: #{worst[0]} (+{worst[1]} vs par)")

    # Front 9 vs Back 9 (only if we have holes in both)
    front_indices = [i for i, h in enumerate(holes_played) if h <= 9]
    back_indices = [i for i, h in enumerate(holes_played) if h >= 10]
    if front_indices and back_indices:
        front_score = sum(scores[i] for i in front_indices)
        back_score = sum(scores[i] for i in back_indices)
        front_par = sum(holes.get(holes_played[i], 4) for i in front_indices)
        back_par = sum(holes.get(holes_played[i], 4) for i in back_indices)
        front_vs_par = front_score - front_par
        back_vs_par = back_score - back_par
        if front_vs_par != back_vs_par:
            if front_vs_par < back_vs_par:
                insights.append(f"Stronger front 9 ({front_vs_par} vs {back_vs_par} on the back)")
            else:
                insights.append(f"Stronger back 9 ({back_vs_par} vs {front_vs_par} on the front)")

    # GIR
    if gir_list and len(gir_list) == num_holes:
        gir_count = sum(gir_list)
        pct = round(100 * gir_count / num_holes)
        if pct >= 50:
            insights.append(f"Hit {gir_count}/{num_holes} greens ({pct}% GIR) — solid approach shots")
        elif gir_count > 0:
            insights.append(f"Hit {gir_count}/{num_holes} greens ({pct}% GIR) — room to improve on approaches")
        else:
            insights.append("No greens in regulation — focus on approach accuracy")

    # Fairways
    if fairway_list:
        fairway_hit = sum(1 for f in fairway_list if f == "hit")
        fairway_total = sum(1 for f in fairway_list if f in ("hit", "left", "right"))
        if fairway_total > 0:
            pct = round(100 * fairway_hit / fairway_total)
            if pct >= 60:
                insights.append(f"Fairways: {fairway_hit}/{fairway_total} ({pct}%) — driving it well")
            elif pct >= 40:
                insights.append(f"Fairways: {fairway_hit}/{fairway_total} ({pct}%)")
            else:
                insights.append(f"Fairways: {fairway_hit}/{fairway_total} ({pct}%) — consider club or aim")

    # Putts
    if putts_list and len(putts_list) == num_holes:
        total_putts = sum(putts_list)
        avg_putts = round(total_putts / num_holes, 1)
        three_putts = sum(1 for p in putts_list if p >= 3)
        insights.append(f"Total putts: {total_putts} ({avg_putts} per hole)")
        if three_putts > 0:
            insights.append(f"Three-putts: {three_putts} — focus on lag putts and short putts")

    # Birdies / pars / bogeys
    if scores_vs_par:
        birdies = sum(1 for svp in scores_vs_par if svp[1] == -1)
        pars = sum(1 for svp in scores_vs_par if svp[1] == 0)
        bogeys = sum(1 for svp in scores_vs_par if svp[1] == 1)
        others = num_holes - birdies - pars - bogeys
        parts = []
        if birdies:
            parts.append(f"{birdies} birdie(s)")
        if pars:
            parts.append(f"{pars} par(s)")
        if bogeys:
            parts.append(f"{bogeys} bogey(s)")
        if others:
            parts.append(f"{others} other(s)")
        if parts:
            insights.append("Scorecard: " + ", ".join(parts))

    return insights


def send_round_email(
    player_name,
    to_email,
    holes_played,
    scores_list,
    gir_list=None,
    fairway_list=None,
    putts_list=None,
    course_info=None,
):
    """
    Sends a summary of a golf round to the specified email.
    holes_played: list of hole numbers (e.g. [1,2,3,...,9] for 9 holes).
    scores_list, gir_list, fairway_list, putts_list are parallel to holes_played.
    """
    total_score = sum(scores_list)
    num_holes = len(holes_played)
    total_par = sum(course_info["holes"].get(h, 4) for h in holes_played) if course_info else 0
    to_par = total_score - total_par

    body = f"Hello {player_name},\n\n"

    if course_info:
        body += f"Course: {course_info['name']}\n"
        body += f"Holes played: {num_holes}\n"
        body += f"Par for round: {total_par}\n"
        if to_par > 0:
            body += f"Score: {total_score} (+{to_par})\n"
        elif to_par < 0:
            body += f"Score: {total_score} ({to_par})\n"
        else:
            body += f"Score: {total_score} (Even)\n"
        body += "\n"

    body += "Hole-by-Hole Summary:\n"
    body += "-" * 50 + "\n"

    for idx, hole_num in enumerate(holes_played):
        score = scores_list[idx] if idx < len(scores_list) else 0
        hole_info = f"Hole {hole_num}: {score}"
        if course_info:
            par = course_info["holes"].get(hole_num, 4)
            hole_info += f" (Par {par})"

        if putts_list and idx < len(putts_list):
            hole_info += f" Putts: {putts_list[idx]}"

        if gir_list and idx < len(gir_list):
            gir = "✓" if gir_list[idx] else "✗"
            hole_info += f" GIR: {gir}"

        if fairway_list and idx < len(fairway_list) and course_info:
            par = course_info["holes"].get(hole_num, 4)
            if par != 3:
                fairway = fairway_list[idx]
                if fairway == "hit":
                    hole_info += " Fairway: ✓"
                elif fairway == "left":
                    hole_info += " Fairway: ←"
                elif fairway == "right":
                    hole_info += " Fairway: →"
                else:
                    hole_info += " Fairway: -"

        body += hole_info + "\n"

    body += "\n" + "-" * 50 + "\n"
    body += f"Total Score: {total_score}\n"

    if gir_list:
        gir_count = sum(gir_list)
        body += f"Greens in Regulation: {gir_count}/{num_holes}\n"

    if fairway_list:
        fairway_hit = sum(1 for f in fairway_list if f == "hit")
        fairway_left = sum(1 for f in fairway_list if f == "left")
        fairway_right = sum(1 for f in fairway_list if f == "right")
        fairway_total = fairway_hit + fairway_left + fairway_right
        if fairway_total > 0:
            body += f"Fairways Hit: {fairway_hit}/{fairway_total} ({fairway_left} left, {fairway_right} right)\n"

    if putts_list and len(putts_list) == num_holes:
        total_putts = sum(putts_list)
        body += f"Total Putts: {total_putts} ({round(total_putts / num_holes, 1)} per hole)\n"

    insights = _build_insights(
        holes_played,
        scores_list,
        putts_list or [],
        gir_list or [],
        fairway_list or [],
        course_info,
    )
    if insights:
        body += "\n" + "-" * 50 + "\n"
        body += "Insights:\n"
        for line in insights:
            body += f"• {line}\n"

    body += "\nThanks for using Golf Tracker!"

    msg = MIMEText(body)
    subject = f"{player_name}'s Golf Round Summary"
    if num_holes != 18:
        subject += f" ({num_holes} holes)"
    msg["Subject"] = subject
    msg["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
    msg["To"] = to_email

    try:
        with smtplib.SMTP(current_app.config["MAIL_SERVER"], current_app.config["MAIL_PORT"]) as server:
            server.starttls()
            server.login(current_app.config["MAIL_USERNAME"], current_app.config["MAIL_PASSWORD"])
            server.sendmail(msg["From"], [to_email], msg.as_string())
        return 200, "Email sent successfully"
    except Exception as e:
        return 500, str(e)


def send_test_email(to_email):
    """Send a simple test email to verify SMTP config."""
    try:
        with smtplib.SMTP(current_app.config["MAIL_SERVER"], current_app.config["MAIL_PORT"]) as server:
            server.starttls()
            server.login(current_app.config["MAIL_USERNAME"], current_app.config["MAIL_PASSWORD"])
            msg = MIMEText("Golf Tracker test email – config is working.")
            msg["Subject"] = "Golf Tracker test"
            msg["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
            msg["To"] = to_email
            server.sendmail(msg["From"], [to_email], msg.as_string())
        return 200, "Test email sent"
    except Exception as e:
        return 500, str(e)
