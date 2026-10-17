import base64
import json
import unittest
from unittest import mock

from app import app
from app import backup
from app.courses import build_snapshot, import_course
from app.rounds import export_rounds, get_round, save_round
from tests.helpers import memory_db, sample_course


class FakeGitHub:
    """Minimal stand-in for the GitHub contents API."""

    def __init__(self, data=None, conflicts=0):
        self.data = data
        self.sha = "sha0" if data else None
        self.conflicts = conflicts
        self.puts = []

    def _resp(self, status, payload=None, raw=None):
        r = mock.Mock(status_code=status)
        r.json.return_value = payload
        r.content = raw
        r.raise_for_status = mock.Mock(side_effect=None if status < 400 else Exception(status))
        return r

    def get(self, url, headers, params, timeout):
        if self.data is None:
            return self._resp(404)
        if "raw" in headers["Accept"]:
            return self._resp(200, raw=json.dumps(self.data).encode())
        return self._resp(200, {"sha": self.sha})

    def put(self, url, headers, json, timeout):
        if self.conflicts:
            self.conflicts -= 1
            return self._resp(409)
        self.puts.append(json)
        self.data = __import__("json").loads(base64.b64decode(json["content"]))
        self.sha = f"sha{len(self.puts)}"
        return self._resp(201)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        import_course(self.conn, sample_course())
        self.snap = build_snapshot(self.conn, 1, 1)
        app.config.update(BACKUP_GITHUB_TOKEN="t", BACKUP_GITHUB_REPO="me/golf-data")

    def tearDown(self):
        app.config.update(BACKUP_GITHUB_TOKEN=None, BACKUP_GITHUB_REPO=None)

    def _save(self, rid, name):
        save_round(self.conn, rid * 32, self.snap, [{"hole_number": 1, "score": 4}], name, "2026-10-01")

    def run_with(self, gh, fn):
        with app.app_context(), mock.patch.object(backup.requests, "get", gh.get), \
                mock.patch.object(backup.requests, "put", gh.put):
            return fn(self.conn)

    def test_disabled_without_config(self):
        app.config.update(BACKUP_GITHUB_TOKEN=None)
        with app.app_context():
            self.assertFalse(backup.backup_now(self.conn))
            self.assertEqual(backup.restore_if_empty(self.conn), 0)

    def test_backup_creates_file(self):
        self._save("a", "Alice")
        gh = FakeGitHub()
        self.assertTrue(self.run_with(gh, backup.backup_now))
        self.assertEqual(len(gh.data["rounds"]), 1)
        self.assertNotIn("sha", gh.puts[0])
        self.assertEqual(gh.puts[0]["branch"], "main")

    def test_backup_merges_remote_rounds_first(self):
        other = memory_db()
        import_course(other, sample_course())
        save_round(other, "r" * 32, build_snapshot(other, 1, 1), [{"hole_number": 1, "score": 3}], "Remote", "2026-09-01")
        gh = FakeGitHub(export_rounds(other))
        self._save("a", "Local")  # e.g. a restart whose restore failed, then a new round
        self.assertTrue(self.run_with(gh, backup.backup_now))
        names = sorted(r["player_name"] for r in gh.data["rounds"])
        self.assertEqual(names, ["Local", "Remote"])  # remote round not lost
        self.assertEqual(gh.puts[0]["sha"], "sha0")
        self.assertIsNotNone(get_round(self.conn, "r" * 32))

    def test_conflict_is_retried(self):
        self._save("a", "Alice")
        gh = FakeGitHub(conflicts=1)
        self.assertTrue(self.run_with(gh, backup.backup_now))
        self.assertEqual(len(gh.puts), 1)

    def test_network_failure_does_not_raise(self):
        self._save("a", "Alice")
        with app.app_context(), mock.patch.object(backup.requests, "get", side_effect=OSError("down")):
            self.assertFalse(backup.backup_now(self.conn))
            empty = memory_db()
            self.assertEqual(backup.restore_if_empty(empty), 0)

    def test_restore_only_into_empty_database(self):
        other = memory_db()
        import_course(other, sample_course())
        save_round(other, "r" * 32, build_snapshot(other, 1, 1), [{"hole_number": 1, "score": 3}], "Remote", "2026-09-01")
        gh = FakeGitHub(export_rounds(other))
        self.assertEqual(self.run_with(gh, backup.restore_if_empty), 1)
        self.assertEqual(get_round(self.conn, "r" * 32)["player_name"], "Remote")
        self.assertEqual(self.run_with(gh, backup.restore_if_empty), 0)  # not empty any more


if __name__ == "__main__":
    unittest.main()
