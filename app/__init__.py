import os
from flask import Flask
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()


def create_app():
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Sessions and form protection need it; "
            "set it in the environment (see .env.example)."
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "1") == "1"

    # Mailjet send API credentials (the API key and secret)
    app.config["MAIL_USERNAME"] = os.getenv("MAILJET_SMTP_USERNAME")
    app.config["MAIL_PASSWORD"] = os.getenv("MAILJET_SMTP_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_FROM_EMAIL")

    # Email abuse limits (see app/security.py)
    app.config["EMAIL_LIMIT_PER_ROUND"] = int(os.getenv("EMAIL_LIMIT_PER_ROUND", "2"))
    app.config["EMAIL_LIMIT_PER_CLIENT_HOUR"] = int(os.getenv("EMAIL_LIMIT_PER_CLIENT_HOUR", "5"))
    app.config["EMAIL_LIMIT_PER_DAY"] = int(os.getenv("EMAIL_LIMIT_PER_DAY", "50"))

    # SQLite file for course data and saved rounds (defaults to instance/golf_tracker.db)
    app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH")

    # Optional backup of saved rounds to a GitHub repository (see app/backup.py)
    app.config["BACKUP_GITHUB_TOKEN"] = os.getenv("BACKUP_GITHUB_TOKEN")
    app.config["BACKUP_GITHUB_REPO"] = os.getenv("BACKUP_GITHUB_REPO")
    app.config["BACKUP_GITHUB_BRANCH"] = os.getenv("BACKUP_GITHUB_BRANCH", "main")
    app.config["BACKUP_GITHUB_PATH"] = os.getenv("BACKUP_GITHUB_PATH", "golf-tracker/rounds.json")

    # Render terminates TLS and adds one X-Forwarded-For hop; trust exactly one
    # so request.remote_addr is the real client address for rate limiting.
    if os.getenv("TRUST_PROXY", "1") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from app import db
    db.init_app(app)

    from app import security
    security.init_app(app)

    from app.cli import courses_cli, backup_cli, send_test_email_command
    app.cli.add_command(courses_cli)
    app.cli.add_command(backup_cli)
    app.cli.add_command(send_test_email_command)

    from app.routes.main import main
    app.register_blueprint(main)

    return app


app = create_app()
