import os
from flask import Flask
from app.email_utils import send_round_email

app = Flask(__name__)
app.config["MAIL_SERVER"] = "localhost"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "user"
app.config["MAIL_PASSWORD"] = "pass"
app.config["MAIL_DEFAULT_SENDER"] = "test@example.com"

with app.app_context():
    scores = [5, 4]
    gir_list = [True, True]
    fairway_list = ["hit", "hit"]
    putts_list = [2, 2]
    course_info = {
        "name": "Test Course",
        "total_par": 72,
        "holes": {1: 5, 2: 4}
    }

    try:
        print("Testing send_round_email...")
        status, message = send_round_email(
            "Golfer",
            "user@example.com",
            scores,
            gir_list,
            fairway_list,
            putts_list,
            course_info
        )
        print(f"Status: {status}")
        print(f"Message: {message}")
    except Exception as e:
        print(f"CRASHED: {e}")
        import traceback
        traceback.print_exc()
