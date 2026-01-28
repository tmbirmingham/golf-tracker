from flask import Blueprint
from app.email_utils import send_test_email
import os

main = Blueprint("main", __name__)

@main.route("/")
def index():
    return "⛳ Golf Tracker is running"

@main.route("/test-email")
def test_email():
    status, response = send_test_email(os.getenv("MAIL_FROM_EMAIL"))
    return f"Status: {status}<br>{response}"
