import re
import sqlite3

from app.db import migrate

PARS = [4, 4, 3, 5, 4, 4, 3, 5, 4, 4, 4, 3, 5, 4, 4, 3, 5, 4]  # 72
YARDS = [400, 380, 160, 520, 410, 390, 170, 530, 420] * 2  # 3380 * 2


def sample_course(**overrides):
    """Fictional course used only by tests."""
    data = {
        "slug": "test-links",
        "name": "Test Links",
        "published_par": 72,
        "provenance": {"holes.par": {"status": "verified", "source": "test"}},
        "holes": [{"number": i + 1, "par": p, "handicap": i + 1} for i, p in enumerate(PARS)],
        "tees": [{
            "name": "Blue",
            "status": "verified",
            "course_rating": 71.5,
            "slope_rating": 128,
            "published_par": 72,
            "published_yardage": sum(YARDS),
            "holes": [{"number": i + 1, "yardage": y} for i, y in enumerate(YARDS)],
        }],
    }
    data.update(overrides)
    return data


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def play_round(client, course_tee, name="Pat Golfer", scores=None, played_on="2026-10-01", putts=1):
    """Start a round through the UI and enter scores {hole: strokes}. Returns the csrf token."""
    token = csrf(client)
    r = client.post("/", data={"csrf_token": token, "player_name": name, "played_on": played_on,
                               "course_tee": course_tee, "confirm_discard": "yes"})
    assert r.status_code == 302, r.get_data(as_text=True)
    for hole, score in (scores or {}).items():
        r = client.post(f"/hole/{hole}", data={"csrf_token": token, "score": str(score),
                                               "putts": str(putts), "action": "card"})
        assert r.status_code == 302, r.get_data(as_text=True)
    return token


def save(client, token):
    r = client.post("/finish", data={"csrf_token": token})
    assert r.status_code == 302, r.get_data(as_text=True)
    return r.headers["Location"].rsplit("/", 1)[-1]
