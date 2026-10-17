"""
Form protection and email rate limiting.

CSRF: every POST must carry the per-session token rendered by csrf_field().
Email limits: sends are counted in email_log (no addresses stored; the client
address is kept only as a hash keyed with SECRET_KEY).
"""
import hashlib
import hmac
import secrets

from flask import abort, current_app, request, session
from markupsafe import Markup

from app.db import get_db

CSRF_SESSION_KEY = "_csrf"


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field():
    return Markup('<input type="hidden" name="csrf_token" value="%s">' % csrf_token())


def _check_csrf():
    if request.method != "POST":
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, sent):
        abort(400, description="This form has expired. Go back, reload the page and try again.")


def client_hash():
    key = current_app.config["SECRET_KEY"].encode()
    addr = (request.remote_addr or "unknown").encode()
    return hmac.new(key, addr, hashlib.sha256).hexdigest()


def email_allowed(round_id):
    """Return (allowed, reason). reason is safe to show the user."""
    cfg = current_app.config
    db = get_db()
    per_round = db.execute(
        "SELECT COUNT(*) FROM email_log WHERE round_id = ?", (round_id,)
    ).fetchone()[0]
    if per_round >= cfg["EMAIL_LIMIT_PER_ROUND"]:
        return False, "This round has already been emailed the maximum number of times."
    per_client = db.execute(
        "SELECT COUNT(*) FROM email_log WHERE client_hash = ? AND sent_at > datetime('now', '-1 hour')",
        (client_hash(),),
    ).fetchone()[0]
    if per_client >= cfg["EMAIL_LIMIT_PER_CLIENT_HOUR"]:
        return False, "Too many emails sent from this device. Please try again in an hour."
    per_day = db.execute(
        "SELECT COUNT(*) FROM email_log WHERE sent_at > datetime('now', '-1 day')"
    ).fetchone()[0]
    if per_day >= cfg["EMAIL_LIMIT_PER_DAY"]:
        return False, "Email is temporarily unavailable. Your round is saved."
    return True, None


def record_email(round_id):
    db = get_db()
    db.execute("INSERT INTO email_log (round_id, client_hash) VALUES (?, ?)", (round_id, client_hash()))
    # Old entries aren't needed for any limit.
    db.execute("DELETE FROM email_log WHERE sent_at < datetime('now', '-7 days')")
    db.commit()


def init_app(app):
    app.before_request(_check_csrf)
    app.jinja_env.globals["csrf_field"] = csrf_field

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
