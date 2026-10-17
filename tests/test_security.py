import os
import unittest
from unittest import mock

from app import app
from app.courses import import_course
from app.db import connect
from tests.helpers import csrf, play_round, sample_course, save

EMAIL = "secret.person@example.com"


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = connect(os.environ["DATABASE_PATH"])
        import_course(conn, sample_course(slug="security-links", name="Security Links"))
        cls.course_tee = "%d:%d" % tuple(conn.execute(
            "SELECT c.id, t.id FROM courses c JOIN tees t ON t.course_id = c.id WHERE c.slug = 'security-links'"
        ).fetchone())
        conn.close()

    def setUp(self):
        self.client = app.test_client()
        conn = connect(os.environ["DATABASE_PATH"])
        conn.execute("DELETE FROM email_log")
        conn.commit()
        conn.close()

    def _saved_round(self, client=None):
        client = client or self.client
        token = play_round(client, self.course_tee, scores={1: 4, 2: 5})
        return save(client, token), token

    def test_post_without_csrf_token_rejected(self):
        for path in ("/", "/finish", "/hole/1"):
            self.assertEqual(self.client.post(path, data={"player_name": "x"}).status_code, 400, path)

    def test_test_email_page_removed(self):
        self.assertEqual(self.client.get("/test-email").status_code, 404)

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_email_only_for_round_just_saved_in_this_browser(self, send):
        round_id, token = self._saved_round()
        stranger = app.test_client()
        stranger_token = csrf(stranger)
        r = stranger.post(f"/rounds/{round_id}/email", data={"csrf_token": stranger_token, "email": EMAIL})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("Email me a copy", stranger.get(f"/rounds/{round_id}").get_data(as_text=True))
        send.assert_not_called()

        self.assertIn("Email me a copy", self.client.get(f"/rounds/{round_id}").get_data(as_text=True))
        r = self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": EMAIL})
        self.assertEqual(r.status_code, 302)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "Pat Golfer")

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_email_needs_a_finished_round(self, send):
        token = play_round(self.client, self.course_tee, scores={1: 4})  # not saved yet
        r = self.client.post(f"/rounds/{'f' * 32}/email", data={"csrf_token": token, "email": EMAIL})
        self.assertEqual(r.status_code, 403)
        send.assert_not_called()

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_per_round_limit(self, send):
        round_id, token = self._saved_round()
        for _ in range(4):
            self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": EMAIL})
        self.assertEqual(send.call_count, app.config["EMAIL_LIMIT_PER_ROUND"])

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_per_client_limit_across_rounds(self, send):
        for _ in range(4):  # 4 rounds x 2 emails, same client address
            round_id, token = self._saved_round()
            for _ in range(2):
                self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": EMAIL})
        self.assertEqual(send.call_count, app.config["EMAIL_LIMIT_PER_CLIENT_HOUR"])

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_invalid_address_rejected(self, send):
        round_id, token = self._saved_round()
        for bad in ("not-an-email", "a@b", "x" * 250 + "@example.com", "a@b.com\nBcc: c@d.com"):
            self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": bad})
        send.assert_not_called()

    @mock.patch("app.routes.main.send_round_email", return_value=(500, "Mailjet API key or secret not configured"))
    def test_provider_errors_not_shown(self, send):
        round_id, token = self._saved_round()
        r = self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": EMAIL},
                             follow_redirects=True)
        page = r.get_data(as_text=True)
        self.assertNotIn("API key", page)
        self.assertIn("couldn&#39;t be sent", page)

    @mock.patch("app.routes.main.send_round_email", return_value=(200, "ok"))
    def test_email_address_never_stored_or_shown(self, send):
        round_id, token = self._saved_round()
        self.client.post(f"/rounds/{round_id}/email", data={"csrf_token": token, "email": EMAIL})
        conn = connect(os.environ["DATABASE_PATH"])
        dump = "\n".join(conn.iterdump())
        conn.close()
        self.assertNotIn("secret.person", dump)
        public = app.test_client()
        for path in ("/rounds", "/rounds?view=best", f"/rounds/{round_id}", "/courses"):
            self.assertNotIn("secret.person", public.get(path).get_data(as_text=True), path)

    def test_security_headers(self):
        r = self.client.get("/")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_player_name_is_escaped(self):
        token = play_round(self.client, self.course_tee, name="<script>alert(1)</script>", scores={1: 4})
        round_id = save(self.client, token)
        page = app.test_client().get(f"/rounds/{round_id}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main()
