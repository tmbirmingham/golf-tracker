import os
import unittest

from app import app
from app.courses import (
    CourseDataError, build_snapshot, bundled_course_files, import_course, load_course_file,
    seed_bundled_courses, validate_course_data,
)
from app.db import connect
from tests.helpers import YARDS, csrf, memory_db, play_round, sample_course, save


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


class ArrowHeadDataTests(unittest.TestCase):
    def setUp(self):
        self.data = load_course_file(
            os.path.join(os.path.dirname(__file__), "..", "app", "data", "courses",
                         "arrowhead-golf-club-naples.json"))

    def test_published_totals(self):
        tees = {t["name"]: t for t in self.data["tees"]}
        expected = {
            "Championship": (6832, 3363, 73.1, 130), "Tournament": (6453, 3227, 71.1, 128),
            "Back": (6011, 3003, 69.1, 124), "Middle": (5243, 2657, 65.9, 114),
            "Middle (L)": (5243, 2657, 71.0, 123), "Forward": (4604, 2232, 67.1, 112),
        }
        self.assertEqual(set(tees), set(expected))
        for name, (total, front, rating, slope) in expected.items():
            ys = [h["yardage"] for h in tees[name]["holes"]]
            self.assertEqual(sum(ys), total, name)
            self.assertEqual(sum(ys[:9]), front, name)
            self.assertEqual((tees[name]["course_rating"], tees[name]["slope_rating"]), (rating, slope))
        pars = [h["par"] for h in self.data["holes"]]
        self.assertEqual((sum(pars[:9]), sum(pars[9:])), (36, 36))
        self.assertEqual(sorted(h["handicap"] for h in self.data["holes"]), list(range(1, 19)))

    def test_newer_data_version_updates_existing_course(self):
        conn = memory_db()
        old = dict(self.data, data_version=1, tees=[], published_par=None)
        old["holes"] = [dict(h, handicap=None) for h in self.data["holes"]]
        import_course(conn, old)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tees").fetchone()[0], 0)
        seed_bundled_courses(conn)  # bundled file is data_version 2
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tees").fetchone()[0], 6)
        self.assertEqual(conn.execute("SELECT data_version FROM courses").fetchone()[0], 2)
        seed_bundled_courses(conn)  # same version again: no-op
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tees").fetchone()[0], 6)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()

    def tearDown(self):
        self.conn.close()

    def test_import_skips_existing_without_update(self):
        self.assertEqual(import_course(self.conn, sample_course()), "created")
        self.assertEqual(import_course(self.conn, sample_course(name="Changed")), "skipped")
        self.assertEqual(self.conn.execute("SELECT name FROM courses").fetchone()[0], "Test Links")

    def test_invalid_data_is_not_imported(self):
        with self.assertRaises(CourseDataError):
            import_course(self.conn, sample_course(published_par=70))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 0)

    def test_update_keeps_round_history(self):
        from app.rounds import get_round, save_round
        import_course(self.conn, sample_course())
        course_id = self.conn.execute("SELECT id FROM courses").fetchone()[0]
        tee_id = self.conn.execute("SELECT id FROM tees").fetchone()[0]
        snap = build_snapshot(self.conn, course_id, tee_id)
        save_round(self.conn, "a" * 32, snap, [{"hole_number": 1, "score": 4}], "Pat", "2026-10-01")

        # Course is later re-published with hole 1 as a par 5 and a longer tee.
        updated = sample_course(published_par=73)
        updated["holes"][0]["par"] = 5
        updated["tees"][0]["published_par"] = 73
        updated["tees"][0]["holes"][0]["yardage"] = 500
        updated["tees"][0]["published_yardage"] += 100
        self.assertEqual(import_course(self.conn, updated, update=True), "updated")

        self.assertEqual(build_snapshot(self.conn, course_id, tee_id)["holes"][0]["par"], 5)
        rnd = get_round(self.conn, "a" * 32)
        self.assertEqual(rnd["snapshot"]["holes"][0], {"number": 1, "par": 4, "handicap": 1, "yardage": 400})
        self.assertEqual(rnd["to_par"], 0)
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

    def test_arrowhead_is_seeded_with_tees(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("ArrowHead Golf Club", page)
        self.assertIn("Championship (Gold) · 6,832 yds · 73.1/130", page)

    def test_round_with_tee_shows_yardage_and_saves(self):
        before = self._course_tables()
        token = play_round(self.client, f"{self.course_id}:{self.tee_id}", scores={})

        page = self.client.get("/hole/1").get_data(as_text=True)
        self.assertIn("400 yds", page)
        self.assertIn("Hcp 1", page)

        self.client.post("/hole/1", data={"csrf_token": token, "score": "5", "putts": "2",
                                          "fairway": "hit", "gir": "yes", "action": "card"})
        self.assertIn('value="5"', self.client.get("/hole/1").get_data(as_text=True))

        round_id = save(self.client, token)
        page = self.client.get(f"/rounds/{round_id}").get_data(as_text=True)
        self.assertIn("Pat Golfer", page)
        self.assertIn("Blue tees", page)
        self.assertEqual(self._course_tables(), before)

    def test_invalid_tee_rejected(self):
        token = csrf(self.client)
        r = self.client.post("/", data={"csrf_token": token, "player_name": "X", "played_on": "2026-10-01",
                                         "course_tee": f"{self.course_id}:99999"})
        self.assertEqual(r.status_code, 400)

    def test_legacy_session_without_course_uses_default(self):
        with self.client.session_transaction() as s:
            s["scores"] = {"1": 5}
            s["gir"], s["fairway"], s["putts"] = {}, {}, {}
        page = self.client.get("/round").get_data(as_text=True)
        self.assertIn("ArrowHead Golf Club", page)


if __name__ == "__main__":
    unittest.main()
