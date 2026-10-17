import json
import os
import sqlite3
import unittest

from app import app
from app.courses import build_snapshot, import_course
from app.db import MIGRATIONS, _statements, connect, migrate
from app.rounds import (
    clean_player_name, export_rounds, get_round, import_rounds, player_key, query_rounds,
    round_filters, save_round,
)
from tests.helpers import PARS, csrf, memory_db, play_round, sample_course, save


def entries(scores):
    return [{"hole_number": h, "score": s} for h, s in scores.items()]


class NameTests(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean_player_name("  Tim\t  Birmingham \n"), "Tim Birmingham")
        self.assertEqual(clean_player_name("ＴＩＭ"), "TIM")  # full-width folded by NFKC
        self.assertIsNone(clean_player_name("   "))
        self.assertIsNone(clean_player_name("\x00\x07"))
        self.assertEqual(len(clean_player_name("x" * 100)), 40)

    def test_key_is_case_and_accent_insensitive(self):
        self.assertEqual(player_key("José Núñez"), player_key("jose nunez"))
        self.assertNotEqual(player_key("Tim B"), player_key("Tim C"))


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        import_course(self.conn, sample_course())
        self.snap = build_snapshot(self.conn, 1, 1)
        full_par = {h: p for h, p in enumerate(PARS, 1)}
        # 18 holes, +3 / 18 holes, -1 / 9 holes, even / 12-hole partial, +0
        self._save("a", "Alice", "2026-09-01", {h: p + (1 if h <= 3 else 0) for h, p in full_par.items()})
        self._save("b", "Bob", "2026-09-15", {h: p - (1 if h == 5 else 0) for h, p in full_par.items()})
        self._save("c", "alice", "2026-10-01", {h: full_par[h] for h in range(1, 10)})
        self._save("d", "Carol", "2026-10-05", {h: full_par[h] for h in range(1, 13)})

    def _save(self, rid, name, day, scores):
        save_round(self.conn, rid * 32, self.snap, entries(scores), name, day)

    def query(self, **args):
        rows, total = query_rounds(self.conn, round_filters(args))
        return [r["player_name"] for r in rows], total, rows

    def test_totals_and_to_par(self):
        rnd = get_round(self.conn, "a" * 32)
        self.assertEqual((rnd["total_score"], rnd["total_par"], rnd["to_par"]), (75, 72, 3))
        partial = get_round(self.conn, "d" * 32)
        self.assertEqual(partial["holes_played"], 12)
        self.assertEqual(partial["total_par"], sum(PARS[:12]))
        self.assertEqual(partial["to_par"], 0)

    def test_recent_is_newest_first_and_includes_everything(self):
        names, total, _ = self.query()
        self.assertEqual(names, ["Carol", "alice", "Bob", "Alice"])
        self.assertEqual(total, 4)

    def test_best_ranks_complete_rounds_only(self):
        names, _, rows = self.query(view="best")
        self.assertEqual(names, ["Bob", "Alice"])  # 18-hole only; partial and 9-hole excluded
        self.assertEqual([r["to_par"] for r in rows], [-1, 3])
        self.assertEqual([r["rank"] for r in rows], [1, 2])
        self._save("e", "Eve", "2026-10-06", {h: p - (1 if h == 7 else 0) for h, p in enumerate(PARS, 1)})
        _, _, rows = self.query(view="best")
        self.assertEqual([(r["player_name"], r["rank"]) for r in rows][:3], [("Eve", 1), ("Bob", 1), ("Alice", 3)])
        names, _, _ = self.query(view="best", holes="9")
        self.assertEqual(names, ["alice"])

    def test_player_search_is_a_filter(self):
        names, _, _ = self.query(player="ALICE")
        self.assertEqual(sorted(names), ["Alice", "alice"])  # not merged, both listed
        names, _, _ = self.query(player="%")
        self.assertEqual(names, [])  # LIKE wildcards are escaped

    def test_date_course_tee_filters(self):
        names, _, _ = self.query(**{"from": "2026-09-10", "to": "2026-10-01"})
        self.assertEqual(names, ["alice", "Bob"])
        self.assertEqual(self.query(course="999")[1], 0)
        self.assertEqual(self.query(tee="Blue")[1], 4)
        self.assertEqual(self.query(tee="Red")[1], 0)

    def test_bad_filter_values_are_ignored(self):
        f = round_filters({"view": "x", "holes": "7", "page": "-3", "from": "yesterday", "course": "abc"})
        self.assertEqual((f["view"], f["holes"], f["page"], f["date_from"], f["course"]), ("recent", "", 1, "", None))

    def test_pagination(self):
        for i in range(30):
            save_round(self.conn, f"{i:032x}", self.snap, entries({1: 4}), f"P{i}", "2026-01-01")
        rows, total = query_rounds(self.conn, round_filters({"page": "2"}))
        self.assertEqual(total, 34)
        self.assertEqual(len(rows), 9)

    def test_resave_replaces_not_duplicates(self):
        self._save("a", "Alice", "2026-09-01", {1: 3})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0], 4)
        self.assertEqual(get_round(self.conn, "a" * 32)["holes_played"], 1)

    def test_export_import_round_trip(self):
        data = json.loads(json.dumps(export_rounds(self.conn)))
        self.assertEqual(len(data["snapshots"]), 1)  # identical snapshots stored once
        other = memory_db()
        import_course(other, sample_course())
        self.assertEqual(import_rounds(other, data), 4)
        self.assertEqual(import_rounds(other, data), 0)  # idempotent
        self.assertEqual(get_round(other, "b" * 32)["to_par"], -1)
        self.assertEqual(get_round(other, "b" * 32)["player_name"], "Bob")


class MigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_without_losing_rounds(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # A database as created by the previous release (migration 1 only).
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        for stmt in _statements(MIGRATIONS[0][1]):
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        snap = {"course_id": None, "course_name": "Old", "holes": [{"number": 1, "par": 4}, {"number": 2, "par": 5}]}
        conn.execute("INSERT INTO rounds (id, course_name, course_snapshot) VALUES ('old', 'Old', ?)", (json.dumps(snap),))
        conn.executemany("INSERT INTO round_holes (round_id, hole_number, score) VALUES ('old', ?, ?)", [(1, 5), (2, 5)])
        conn.commit()

        migrate(conn)
        migrate(conn)  # repeatable

        rnd = get_round(conn, "old")
        self.assertEqual((rnd["holes_played"], rnd["total_score"], rnd["total_par"]), (2, 10, 9))
        self.assertIsNone(rnd["player_name"])
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
        self.assertEqual(versions, [v for v, _ in MIGRATIONS])


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = connect(os.environ["DATABASE_PATH"])
        import_course(conn, sample_course(slug="recovery-links", name="Recovery Links"))
        cls.course_tee = "%d:%d" % tuple(conn.execute(
            "SELECT c.id, t.id FROM courses c JOIN tees t ON t.course_id = c.id WHERE c.slug = 'recovery-links'"
        ).fetchone())
        conn.close()

    def setUp(self):
        self.client = app.test_client()

    def test_next_hole_button_goes_to_next_hole(self):
        token = play_round(self.client, self.course_tee)
        r = self.client.post("/hole/1", data={"csrf_token": token, "score": "4", "putts": "2", "action": "next"})
        self.assertTrue(r.headers["Location"].endswith("/hole/2"))
        r = self.client.post("/hole/2", data={"csrf_token": token, "score": "4", "putts": "2", "action": "prev"})
        self.assertTrue(r.headers["Location"].endswith("/hole/1"))
        r = self.client.post("/hole/18", data={"csrf_token": token, "score": "4", "putts": "2", "action": "next"})
        self.assertTrue(r.headers["Location"].endswith("/finish"))

    def test_start_page_offers_resume_and_requires_confirm_to_discard(self):
        token = play_round(self.client, self.course_tee, scores={1: 4, 2: 5})
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Resume round", page)
        self.assertIn("2 of 18 holes entered", page)
        r = self.client.post("/", data={"csrf_token": token, "player_name": "Pat", "played_on": "2026-10-02",
                                         "course_tee": self.course_tee})
        self.assertEqual(r.status_code, 400)
        self.assertIn("2 holes entered", self.client.get("/").get_data(as_text=True))  # still there

    def test_invalid_scores_rejected_with_message(self):
        token = play_round(self.client, self.course_tee)
        for score, putts, msg in (("0", "0", "Score must be"), ("21", "1", "Score must be"),
                                  ("abc", "1", "Score must be"), ("3", "3", "Putts must be fewer")):
            r = self.client.post("/hole/1", data={"csrf_token": token, "score": score, "putts": putts})
            self.assertEqual(r.status_code, 400)
            self.assertIn(msg, r.get_data(as_text=True))
        with self.client.session_transaction() as s:
            self.assertEqual(s["scores"], {})

    def test_partial_round_can_be_saved(self):
        token = play_round(self.client, self.course_tee, scores={1: 4, 2: 4, 3: 3})
        page = self.client.get("/finish").get_data(as_text=True)
        self.assertIn("15 holes not entered", page)
        round_id = save(self.client, token)
        page = self.client.get(f"/rounds/{round_id}").get_data(as_text=True)
        self.assertIn("Pat Golfer", page)
        with self.client.session_transaction() as s:
            self.assertNotIn("scores", s)

    def test_double_submit_saves_once(self):
        token = play_round(self.client, self.course_tee, name="Double Tap", scores={1: 4})
        with self.client.session_transaction() as s:
            saved = dict(s)
        first = save(self.client, token)
        with self.client.session_transaction() as s:  # simulate the same form posted twice
            s.update(saved)
        second = save(self.client, token)
        self.assertEqual(first, second)
        conn = connect(os.environ["DATABASE_PATH"])
        n = conn.execute("SELECT COUNT(*) FROM rounds WHERE player_name = 'Double Tap'").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_name_and_date_required(self):
        token = csrf(self.client)
        r = self.client.post("/", data={"csrf_token": token, "player_name": "  ", "played_on": "2999-01-01",
                                         "course_tee": self.course_tee})
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Enter the name", page)
        self.assertIn("not in the future", page)


if __name__ == "__main__":
    unittest.main()
