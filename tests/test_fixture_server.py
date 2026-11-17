import unittest
from pathlib import Path

from tests.fixture_server import BANNER_TEXT, create_fixture_app, main

DATA = Path(__file__).resolve().parent.parent / "data"


class FixtureServer(unittest.TestCase):
    """A fixture page cannot pass for the real dashboard."""

    @classmethod
    def setUpClass(cls):
        cls.client = create_fixture_app(DATA, delay_s=0).test_client()

    def test_every_page_is_labelled(self):
        page = self.client.get("/")
        html = page.get_data(as_text=True)
        self.assertIn(BANNER_TEXT, html)
        self.assertIn(f"<title>{BANNER_TEXT} · ", html)
        self.assertEqual(page.headers["X-Test-Preview"], "1")

    def test_canned_answers_say_they_are_fixtures(self):
        status = self.client.get("/api/coach/status").get_json()
        self.assertEqual((status["provider"], status["test_preview"]), ("fixture", True))
        review = self.client.post("/api/matches/1/coach", json={}).get_json()
        self.assertEqual(review["kind"], "fixture")
        self.assertIn(BANNER_TEXT, review["error"])
        imported = self.client.post("/api/open-match", json={"url": "https://overstat.gg/replay/1/1"})
        self.assertEqual(imported.status_code, 403)
        self.assertIn(BANNER_TEXT, imported.get_json()["message"])

    def test_real_dashboard_ports_are_refused(self):
        for port in ("5050", "5051"):
            with self.assertRaises(SystemExit):
                main(["--port", port])

    def test_real_app_is_unlabelled(self):
        from app import create_app

        page = create_app(DATA, backend="files").test_client().get("/")
        self.assertNotIn(BANNER_TEXT, page.get_data(as_text=True))
        self.assertNotIn("X-Test-Preview", page.headers)
