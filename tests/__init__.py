import os
import tempfile

# Configure the app before any test imports it.
_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_tmp.name, "test.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SESSION_COOKIE_SECURE"] = "0"
for key in ("BACKUP_GITHUB_TOKEN", "BACKUP_GITHUB_REPO", "MAILJET_SMTP_USERNAME", "MAILJET_SMTP_PASSWORD"):
    os.environ.pop(key, None)
