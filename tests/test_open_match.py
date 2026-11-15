"""Open Overstat URLs from fixtures. No live Overstat requests."""

from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from pathlib import Path

from open_match import RateLimited, open_match, parse_overstat_url
from preview_store import configure, configured

REPLAY = "https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3"
SESSION = "https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6"
VIEWER = "https://overstat.gg/replay/23231/3"


def _fixture():
    path = Path(__file__).with_name("replay_fixture.py")
    spec = importlib.util.spec_from_file_location("replay_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = _fixture()


class FakeClient:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> bytes:
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(url)


def _game(game_id: int, number: int, livedata: str, mid: str) -> dict:
    return {
        "id": game_id,
        "game": number,
        "livedata": livedata,
        "mid": mid,
        "map_name": "World's Edge",
        "match_start": 1_700_000_000,
    }


def _stats(game_id: int, quality, *, winner: bool = True) -> bytes:
    game = {"id": game_id}
    if winner:
        game.update(fixture.standings(game_id))
    if quality is not None:
        game["analytics"] = {"qualityScore": quality}
    return json.dumps({"eventId": "Fixture", "games": [game]}).encode()


def _routes(games: list[dict], stats: bytes, raw: bytes | Exception) -> dict:
    # Livedata is checked first: its path also contains ``/api/stats/``.
    return {
        "/api/stats/livedata/": raw,
        "/api/games/": json.dumps(games).encode(),
        "/api/stats/": stats,
    }


class ParseTest(unittest.TestCase):
    def test_the_three_public_shapes(self):
        replay = parse_overstat_url(REPLAY)
        self.assertEqual(replay, {"session_id": 23231, "game_number": 3, "kind": "replay"})
        session = parse_overstat_url(SESSION)
        self.assertEqual(session["session_id"], 23231)
        self.assertIsNone(session["game_number"])
        viewer = parse_overstat_url("http://www.overstat.gg/replay/23231/3")
        self.assertEqual((viewer["session_id"], viewer["game_number"], viewer["kind"]), (23231, 3, "replay"))
        standings = parse_overstat_url("https://overstat.gg/tournament/octanecollegiate/23231/standings/3")
        self.assertEqual(standings["game_number"], 3)

    def test_other_hosts_and_pages_are_rejected(self):
        for url in (
            "https://example.com/tournament/octanecollegiate/23231/replay/3",
            "https://overstat.gg/matches/23231",
            "https://overstat.gg/organizers/octanecollegiate",
            "https://overstat.gg/tournament/octanecollegiate",
        ):
            with self.assertRaises(ValueError):
                parse_overstat_url(url)


class OpenTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.previous = configured()
        configure(self.root / "previews")
        self.saved_dirs = []
        from dashboard_data import expansion_dirs

        self.saved_dirs = list(expansion_dirs())

    def tearDown(self):
        from dashboard_data import clear_expansion_dirs, register_expansion_dir

        clear_expansion_dirs()
        for path in self.saved_dirs:
            register_expansion_dir(path)
        if self.previous is not None:
            configure(self.previous)
        self._tmp.cleanup()

    def _open(self, url, client, game_number=None, database=None, extra_dirs=None):
        saved = []
        def record(import_dir, game_id, connection=None):
            saved.append(game_id)
        dirs = extra_dirs if extra_dirs is not None else [self.root]
        result = open_match(
            url,
            data_dirs=dirs,
            import_dir=self.root / "user-imports",
            cache_dir=self.root / "open-cache",
            client=client,
            game_number=game_number,
            database=record if database is None else database,
        )
        result["_saved_ids"] = saved
        return result

    def test_session_choice_does_not_download_telemetry(self):
        games = [_game(880001, 1, "ld-1", "mid-1"), _game(880002, 2, "ld-2", "mid-2")]
        client = FakeClient(_routes(games, _stats(880001, 9.0), b"{}"))
        result = self._open("https://overstat.gg/tournament/org/4242", client)
        self.assertEqual(result["status"], "choose")
        self.assertEqual([game["game_number"] for game in result["games"]], [1, 2])
        self.assertFalse(any("livedata" in url for url in client.calls))
        self.assertEqual(result["_saved_ids"], [])

    def test_existing_game_opens_before_download(self):
        games = [_game(880001, 3, "ld-1", "mid-1")]
        self._manifest(self.root, 880001, "ld-1", "mid-1", "a" * 64)
        client = FakeClient(_routes(games, _stats(880001, 9.0), b"telemetry"))
        result = self._open(VIEWER.replace("23231", "4242"), client)
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["game_id"], 880001)
        self.assertFalse(any("livedata" in url for url in client.calls))
        self.assertEqual(result["_saved_ids"], [])

    def test_matching_replay_hash_opens_the_stored_game(self):
        raw = json.dumps(fixture.series()).encode()
        from download_match import sha256_bytes

        digest = sha256_bytes(raw)
        other = self.root / "already"
        self._manifest(other, 123, "other-ld", "other-mid", digest)
        games = [_game(880010, 3, "ld-new", "mid-new")]
        client = FakeClient(_routes(games, _stats(880010, 9.0), raw))
        result = self._open("https://overstat.gg/replay/4242/3", client, extra_dirs=[self.root, other])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["game_id"], 123)
        self.assertEqual(result["_saved_ids"], [])
        self.assertFalse((self.root / "user-imports" / "collection" / "manifest.json").exists())

    def test_quality_7_saves_and_a_repeat_does_not_duplicate(self):
        raw = json.dumps(fixture.series()).encode()
        games = [_game(880001, 3, "ld-1", "mid-1")]
        client = FakeClient(_routes(games, _stats(880001, 7.0), raw))
        result = self._open("https://overstat.gg/replay/4242/3", client)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["storage"], "saved")
        self.assertEqual(result["_saved_ids"], [880001])
        manifest = json.loads((self.root / "user-imports" / "collection" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(list(manifest["games"]), ["880001"])
        self.assertEqual(manifest["package"], "user-import")
        self.assertEqual(manifest["games"]["880001"]["acceptance_status"], "accepted")
        again = self._open("https://overstat.gg/replay/4242/3", client)
        self.assertEqual(again["status"], "exists")
        self.assertEqual(again["_saved_ids"], [])
        manifest = json.loads((self.root / "user-imports" / "collection" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(list(manifest["games"]), ["880001"])

    def test_below_threshold_and_unknown_quality_stay_previews(self):
        for game_id, quality in ((880021, 6.9), (880022, None)):
            raw = fixture.tagged(fixture.series(), str(game_id))
            games = [_game(game_id, 1, f"ld-{game_id}", f"mid-{game_id}")]
            client = FakeClient(_routes(games, _stats(game_id, quality), raw))
            result = self._open(f"https://overstat.gg/replay/{7000 + game_id}/1", client)
            self.assertEqual(result["status"], "ready", result)
            self.assertEqual(result["storage"], "preview", result)
            self.assertEqual(result["_saved_ids"], [])
            meta = json.loads((self.root / "previews" / str(game_id) / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["acceptance_status"], "preview")
            self.assertNotEqual(meta.get("reason"), "accepted")
        self.assertFalse((self.root / "user-imports" / "collection" / "manifest.json").exists())

    def test_unverified_winner_is_a_preview(self):
        raw = json.dumps(fixture.without_winner(fixture.series())).encode()
        games = [_game(880030, 1, "ld-30", "mid-30")]
        client = FakeClient(_routes(games, _stats(880030, 8.0), raw))
        result = self._open("https://overstat.gg/replay/7030/1", client)
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(result["storage"], "preview")
        self.assertIn("winner", result["message"].lower())
        self.assertEqual(result["_saved_ids"], [])

    def test_unavailable_telemetry_and_unsupported_url(self):
        games = [_game(880040, 1, None, "mid-40")]
        games[0]["livedata"] = None
        client = FakeClient(_routes(games, _stats(880040, 9.0), b"{}"))
        missing = self._open("https://overstat.gg/replay/7040/4", client)
        self.assertEqual(missing["status"], "unavailable")
        self.assertFalse(any("livedata" in url for url in client.calls))
        rejected = self._open("https://overstat.gg/matches/1", FakeClient({}))
        self.assertEqual(rejected["status"], "error")
        self.assertEqual(rejected["_saved_ids"], [])

    def test_rate_limit_is_retryable_and_not_retried(self):
        client = FakeClient({"/api/games/": RateLimited(12)})
        result = self._open("https://overstat.gg/replay/7050/1", client)
        self.assertEqual(result["status"], "retry")
        self.assertEqual(client.calls, ["https://overstat.gg/api/games/7050"])
        self.assertIn("rate-limited", result["message"])

    def test_replay_rate_limit_does_not_request_the_file_again(self):
        games = [_game(880060, 1, "ld-60", "mid-60")]
        client = FakeClient(_routes(games, _stats(880060, 9.0), RateLimited(3)))
        result = self._open("https://overstat.gg/replay/7060/1", client)
        self.assertEqual(result["status"], "retry")
        self.assertEqual(sum("livedata" in url for url in client.calls), 1)

    def test_concurrent_opens_keep_one_record(self):
        raw = json.dumps(fixture.series()).encode()
        games = [_game(880070, 1, "ld-70", "mid-70")]
        client = FakeClient(_routes(games, _stats(880070, 7.0), raw))
        results = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait(timeout=5)
            results.append(self._open("https://overstat.gg/replay/7070/1", client))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        statuses = {result["status"] for result in results}
        self.assertIn("ready", statuses)
        self.assertTrue(statuses <= {"ready", "exists", "retry"})
        manifest = json.loads((self.root / "user-imports" / "collection" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(list(manifest["games"]), ["880070"])
        self.assertEqual(sum(len(result["_saved_ids"]) for result in results), 1)

    def test_saved_match_reopens_after_a_new_app_and_a_preview_can_be_sought(self):
        from app import create_app

        raw = json.dumps(fixture.series()).encode()
        games = [_game(880080, 1, "ld-80", "mid-80")]
        client = FakeClient(_routes(games, _stats(880080, 7.0), raw))
        saved = self._open("https://overstat.gg/replay/7080/1", client, database=False)
        self.assertEqual(saved["storage"], "saved")
        app = create_app(self.root)
        listed = {row["game_id"] for row in app.test_client().get("/api/matches").get_json()}
        self.assertIn(880080, listed)
        restarted = create_app(self.root)
        listed = {row["game_id"] for row in restarted.test_client().get("/api/matches").get_json()}
        self.assertIn(880080, listed)
        summary = restarted.test_client().get("/api/matches/880080").get_json()
        self.assertEqual(summary["game_id"], 880080)
        frame = restarted.test_client().get(f"/api/matches/880080/at/{summary['playing_start']}").get_json()
        self.assertIn("players", frame)
        low = FakeClient(_routes(
            [_game(880081, 1, "ld-81", "mid-81")],
            _stats(880081, 6.9),
            fixture.tagged(fixture.series(), "preview-880081"),
        ))
        preview = self._open("https://overstat.gg/replay/7081/1", low, database=False)
        self.assertEqual(preview["storage"], "preview")
        # create_app points the preview root at this temp directory's previews.
        configure(self.root / "previews")
        preview_app = create_app(self.root)
        body = preview_app.test_client().get("/api/matches/880081").get_json()
        self.assertEqual(body["collection"], "preview")
        self.assertEqual(body["inference"]["storage"], "preview")
        self.assertIn("has not been established", body["inference"]["limitation"])
        sought = preview_app.test_client().get(f"/api/matches/880081/at/{body['playing_start']}").get_json()
        self.assertGreaterEqual(len(sought["players"]), 1)
        self.assertTrue(sought["squads"])

    def _manifest(self, directory: Path, game_id: int, livedata: str, mid: str, digest: str) -> None:
        path = directory / "collection" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"games": {}}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("games", {})[str(game_id)] = {
            "game_id": game_id,
            "livedata_id": livedata,
            "mid": mid,
            "sha256": digest,
            "acceptance_status": "accepted",
        }
        path.write_text(json.dumps(document), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
