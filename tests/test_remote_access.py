import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

from coach import Coach
from remote_access import CallGate, create_login, load_login

DATA = Path(__file__).resolve().parent.parent / "data"


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def read_login(folder: Path) -> tuple[str, str]:
    lines = dict(line.split(": ", 1) for line in (folder / "credentials.txt").read_text().splitlines())
    return lines["user"], lines["password"]


class Login(unittest.TestCase):
    def test_files_are_private_and_hold_no_plain_password_in_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            create_login(folder)
            user, password = read_login(folder)
            self.assertEqual((folder / "auth.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((folder / "credentials.txt").stat().st_mode & 0o777, 0o600)
            self.assertNotIn(password, (folder / "auth.json").read_text())
            self.assertEqual(load_login(folder / "auth.json")["user"], user)
            self.assertGreaterEqual(len(password), 20)


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class RemoteApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        create_login(folder)
        cls.user, cls.password = read_login(folder)
        remote = {"login": load_login(folder / "auth.json"), "gate": CallGate(folder / "usage.json")}
        cls.client = create_app(DATA, backend="files", remote=remote).test_client()
        cls.local = create_app(DATA, backend="files").test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_kind_of_route_needs_the_login(self):
        for method, path in (("get", "/"), ("get", "/static/map_view.js"), ("get", "/api/matches"),
                             ("get", "/api/coach/status"), ("get", "/api/map-config"), ("get", "/stats"),
                             ("post", "/api/matches/123633/coach"), ("post", "/api/open-match"), ("get", "/nope")):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn("Basic", response.headers["WWW-Authenticate"])

    def test_wrong_login_is_refused_and_right_login_works(self):
        self.assertEqual(self.client.get("/api/matches", headers=basic(self.user, "wrong")).status_code, 401)
        self.assertEqual(self.client.get("/api/matches", headers=basic("someone", self.password)).status_code, 401)
        response = self.client.get("/api/matches", headers=basic(self.user, self.password))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_plain_http_through_the_tunnel_redirects_before_any_login_prompt(self):
        response = self.client.get("/api/matches", headers={"X-Forwarded-Proto": "http"})
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers["Location"].startswith("https://"))
        self.assertNotIn("WWW-Authenticate", response.headers)
        cf = self.client.get("/", headers={"CF-Visitor": '{"scheme":"http"}', "X-Forwarded-Proto": "https"})
        self.assertEqual(cf.status_code, 301)
        secure = self.client.get("/api/matches", headers=basic(self.user, self.password) | {"X-Forwarded-Proto": "https"})
        self.assertIn("Strict-Transport-Security", secure.headers)

    def test_url_import_is_off(self):
        response = self.client.post("/api/open-match", json={"url": "https://overstat.gg/x"}, headers=basic(self.user, self.password))
        self.assertEqual(response.status_code, 403)
        self.assertIn("turned off", response.get_json()["message"])

    def test_local_app_is_unchanged(self):
        self.assertEqual(self.local.get("/api/matches").status_code, 200)
        self.assertNotIn("X-Frame-Options", self.local.get("/api/matches").headers)


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.usage = Path(self.tmp.name) / "usage.json"
        self.now = [1_800_000_000.0]

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, limit=2):
        return CallGate(self.usage, max_concurrent=1, daily_limit=limit, clock=lambda: self.now[0])

    def test_one_call_at_a_time(self):
        gate = self.gate(limit=5)
        self.assertIsNone(gate.acquire())
        self.assertEqual(gate.acquire()["kind"], "busy")
        gate.release()
        self.assertIsNone(gate.acquire())

    def test_daily_limit_survives_restart_and_resets_next_day(self):
        gate = self.gate()
        for _ in range(2):
            self.assertIsNone(gate.acquire())
            gate.release()
        refused = self.gate().acquire()  # a new process reads the same file
        self.assertEqual(refused["kind"], "limit")
        self.assertFalse(refused["retryable"])
        self.now[0] += 86400
        self.assertIsNone(self.gate().acquire())
        self.assertEqual(json.loads(self.usage.read_text())["calls"], 1)


class CoachGate(unittest.TestCase):
    """The gate covers provider calls only; a cached review is served without it."""

    env = {"COACH_PROVIDER": "anthropic", "COACH_MODEL": "test-model", "ANTHROPIC_API_KEY": "x"}

    def test_limit_refuses_new_calls_but_not_cached_reviews(self):
        from coach import CoachError
        from tests.test_coach import good_analysis, tiny_packet

        calls = []
        responses = [CoachError("timeout", "timed out", True), (good_analysis(), {})]

        def provider(config, request):
            calls.append(1)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with tempfile.TemporaryDirectory() as tmp:
            gate = CallGate(Path(tmp) / "usage.json", max_concurrent=1, daily_limit=2)
            coach = Coach(Path(tmp) / "cache", call=provider, env=self.env, gate=gate)
            self.assertEqual(coach.analyze(tiny_packet())["kind"], "timeout")  # a failed call still counts
            self.assertEqual(coach.analyze(tiny_packet())["status"], "ok")
            cached = coach.analyze(tiny_packet())
            self.assertTrue(cached["cached"])
            changed = tiny_packet()
            changed["items"][1]["data"]["damage_taken"] = 440
            refused = coach.analyze(changed)
            self.assertEqual((refused["status"], refused["kind"]), ("error", "limit"))
            self.assertNotIn("analysis", refused)
            self.assertEqual(len(calls), 2)
            self.assertEqual(gate.used_today(), 2)

    def test_busy_while_another_call_runs(self):
        from tests.test_coach import good_analysis, tiny_packet

        started, finish = threading.Event(), threading.Event()

        def slow(config, request):
            started.set()
            finish.wait(5)
            return good_analysis(), {}

        with tempfile.TemporaryDirectory() as tmp:
            coach = Coach(None, call=slow, env=self.env, gate=CallGate(Path(tmp) / "usage.json", daily_limit=5))
            results = []
            worker = threading.Thread(target=lambda: results.append(coach.analyze(tiny_packet())))
            worker.start()
            started.wait(5)
            changed = tiny_packet()
            changed["items"][1]["data"]["damage_taken"] = 440
            busy = coach.analyze(changed)
            finish.set()
            worker.join(5)
            self.assertEqual((busy["kind"], busy["retryable"]), ("busy", True))
            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual(coach.analyze(changed)["status"], "ok")  # slot released after the first call
