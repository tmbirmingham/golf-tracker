import copy
import json
import os
import sqlite3
import tempfile
import unittest

_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_tmp.name, "test.db")
os.environ.setdefault("MAILJET_SMTP_PORT", "587")
os.environ.setdefault("SECRET_KEY", "test")

from app import app  # noqa: E402
from app.courses import (  # noqa: E402
    CourseDataError, build_snapshot, bundled_course_files, import_course, load_course_file,
    validate_course_data,
)
from app.db import connect, migrate  # noqa: E402

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


class ValidationTests(unittest.TestCase):
    def test_valid_course(self):
        self.assertEqual(validate_course_data(sample_course()), [])

    def test_bundled_files_are_valid(self):
        for path in bundled_course_files():
            self.assertEqual(validate_course_data(load_course_file(path)), [], path)

    def test_par_total_mismatch(self):
        errors = validate_course_data(sample_course(published_par=71))
        self.assertTrue(any("published par is 71" in e for e in errors), errors)

    def test_yardage_total_mismatch(self):
        data = sample_course()
        data["tees"][0]["published_yardage"] += 1
        errors = validate_course_data(data)
        self.assertTrue(any("yardages sum" in e for e in errors), errors)

    def test_missing_hole(self):
        data = sample_course()
        data["holes"] = data["holes"][:17]
        self.assertTrue(validate_course_data(data))

    def test_duplicate_handicap(self):
        data = sample_course()
        data["holes"][1]["handicap"] = 1
        self.assertIn("hole handicaps must be unique", validate_course_data(data))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_migrate_is_repeatable(self):
        migrate(self.conn)
        migrate(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)

    def test_import_skips_existing_without_update(self):
        self.assertEqual(import_course(self.conn, sample_course()), "created")
        self.assertEqual(import_course(self.conn, sample_course(name="Changed")), "skipped")
        self.assertEqual(self.conn.execute("SELECT name FROM courses").fetchone()[0], "Test Links")

    def test_invalid_data_is_not_imported(self):
        with self.assertRaises(CourseDataError):
            import_course(self.conn, sample_course(published_par=70))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 0)

    def test_update_keeps_round_history(self):
        from app.rounds import save_round, list_rounds
        import_course(self.conn, sample_course())
        course_id = self.conn.execute("SELECT id FROM courses").fetchone()[0]
        tee_id = self.conn.execute("SELECT id FROM tees").fetchone()[0]
        snap = build_snapshot(self.conn, course_id, tee_id)
        save_round(self.conn, "r1", snap, [{"hole_number": 1, "score": 4}])

        # Course is later re-published with hole 1 as a par 5 and a longer tee.
        updated = sample_course(published_par=73)
        updated["holes"][0]["par"] = 5
        updated["tees"][0]["published_par"] = 73
        updated["tees"][0]["holes"][0]["yardage"] = 500
        updated["tees"][0]["published_yardage"] += 100
        self.assertEqual(import_course(self.conn, updated, update=True), "updated")

        self.assertEqual(build_snapshot(self.conn, course_id, tee_id)["holes"][0]["par"], 5)
        stored = json.loads(self.conn.execute("SELECT course_snapshot FROM rounds").fetchone()[0])
        self.assertEqual(stored["holes"][0], {"number": 1, "par": 4, "handicap": 1, "yardage": 400})
        self.assertEqual(list_rounds(self.conn)[0]["to_par"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tees").fetchone()[0], 1)

    def test_snapshot_totals(self):
        import_course(self.conn, sample_course())
        snap = build_snapshot(self.conn, 1, 1)
        self.assertEqual(snap["total_par"], 72)
        self.assertEqual(snap["total_yardage"], sum(YARDS))
        self.assertEqual(snap["tee_name"], "Blue")
        self.assertIsNone(build_snapshot(self.conn, 1, 999))


class RoundFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = connect(os.environ["DATABASE_PATH"])
        import_course(conn, sample_course())
        cls.course_id, cls.tee_id = conn.execute(
            "SELECT c.id, t.id FROM courses c JOIN tees t ON t.course_id = c.id WHERE c.slug = 'test-links'"
        ).fetchone()
        conn.close()

    def setUp(self):
        self.client = app.test_client()

    def _course_tables(self):
        conn = connect(os.environ["DATABASE_PATH"])
        try:
            return [
                [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in ("courses", "course_holes", "tees", "tee_holes")
            ]
        finally:
            conn.close()

    def test_arrowhead_is_seeded(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("ArrowHead Golf Club", page)
        self.assertIn("Test Links", page)

    def test_round_with_tee_shows_yardage_and_saves(self):
        before = self._course_tables()
        r = self.client.post("/", data={"course_tee": f"{self.course_id}:{self.tee_id}"})
        self.assertEqual(r.status_code, 302)

        page = self.client.get("/hole/1").get_data(as_text=True)
        self.assertIn("400 yds", page)
        self.assertIn("Hcp 1", page)
        self.assertIn("Blue tees", page)

        self.client.post("/hole/1", data={"score": "5", "putts": "2", "fairway": "hit", "gir": "yes"})
        page = self.client.get("/hole/1").get_data(as_text=True)
        self.assertIn('value="5"', page)

        page = self.client.get("/finish").get_data(as_text=True)
        self.assertIn("Par 72", page)
        r = self.client.post("/finish", data={"action": "save_only"})
        self.assertIn("Round saved", r.get_data(as_text=True))

        history = self.client.get("/rounds").get_data(as_text=True)
        self.assertIn("Test Links (Blue)", history)
        self.assertEqual(self._course_tables(), before)

    def test_invalid_tee_rejected(self):
        r = self.client.post("/", data={"course_tee": f"{self.course_id}:99999"})
        self.assertEqual(r.status_code, 400)

    def test_legacy_session_without_course_uses_default(self):
        with self.client.session_transaction() as s:
            s["scores"] = {"1": 5}
            s["gir"], s["fairway"], s["putts"] = {}, {}, {}
        page = self.client.get("/round").get_data(as_text=True)
        self.assertIn("ArrowHead Golf Club", page)
        self.assertIn("entered manually", page)


if __name__ == "__main__":
    unittest.main()
