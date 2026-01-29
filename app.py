"""
Entry point for local development: python app.py
On Render, gunicorn loads the package: gunicorn app:app
"""
from app import app

if __name__ == "__main__":
    app.run(debug=True)
