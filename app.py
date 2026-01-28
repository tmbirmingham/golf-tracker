from flask import Flask
from dotenv import load_dotenv
import os

load_dotenv()

def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

    # Mail config (won't break if unused yet)
    app.config["MAIL_SERVER"] = os.getenv("MAILJET_SMTP_SERVER")
    app.config["MAIL_PORT"] = int(os.getenv("MAILJET_SMTP_PORT"))
    app.config["MAIL_USE_TLS"] = True
    app.config["MAIL_USERNAME"] = os.getenv("MAILJET_SMTP_USERNAME")
    app.config["MAIL_PASSWORD"] = os.getenv("MAILJET_SMTP_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_FROM_EMAIL")

    from app.routes import main
    app.register_blueprint(main)

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
