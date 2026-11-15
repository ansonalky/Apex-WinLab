"""Quality filter, dedupe, and resume. No network."""

from __future__ import annotations

import email.utils
import tempfile
import unittest
import urllib.error
from pathlib import Path

from collect_matches import fetch_telemetry, search_url
from collection import (
    decide,
    download_gate,
    game_quality,
    mark_duplicates,
    quality_reason,
    resolve_winner,
    session_quality,
)
from overstat_patch import export_players


class QualityTest(unittest.TestCase):
    def test_threshold_is_inclusive_and_unknown_is_not_zero(self):
        self.assertIsNone(quality_reason(7.0, 7.0))
        self.assertEqual(quality_reason(6.999, 7.0), "below_quality_threshold")
        self.assertEqual(quality_reason(0.0, 7.0), "below_quality_threshold")
        self.assertEqual(quality_reason(None, 7.0), "quality_unknown")
        self.assertIsNone(game_quality({"analytics": {}}))
        self.assertEqual(game_quality({"analytics": {"qualityScore": 0}}), 0.0)
        self.assertEqual(download_gate({"quality_score": 6.9, "livedata_id": "x"}, 7.0), "below_quality_threshold")
        self.assertEqual(download_gate({"quality_score": 0, "livedata_id": "x"}, 7.0), "below_quality_threshold")
        self.assertIsNone(download_gate({"quality_score": 7, "livedata_id": "x"}, 7.0))
        self.assertEqual(download_gate({"quality_score": None, "livedata_id": "x"}, 7.0), "quality_unknown")
        self.assertIn("name=", search_url(0, 20, "", ""))
        self.assertIn("organizer=octane", search_url(20, 20, "ALGS", "octane"))

    def test_session_score_is_not_used_as_the_game_score(self):
        stats = {"analytics": {"qualityScore": 9.5}, "games": [{"id": 1, "analytics": {}}]}
        self.assertEqual(session_quality(stats), 9.5)
        self.assertIsNone(game_quality(stats["games"][0]))
        record = {"quality_score": None, "livedata_id": "abc", "download_status": "downloaded"}
        status, reason = decide(record, {"ok": True, "reason": "accepted"}, 7.0)
        self.assertEqual((status, reason), ("excluded", "quality_unknown"))

    def test_passing_score_still_needs_validation(self):
        record = {"quality_score": 8.0, "livedata_id": "abc", "download_status": "downloaded"}
        status, reason = decide(record, {"ok": False, "reason": "outcome_unknown"}, 7.0)
        self.assertEqual((status, reason), ("excluded", "outcome_unknown"))
        status, reason = decide(record, {"ok": True, "reason": "accepted"}, 7.0)
        self.assertEqual((status, reason), ("accepted", "accepted"))


class DedupeTest(unittest.TestCase):
    def test_same_livedata_on_two_game_ids_is_an_inconsistent_duplicate(self):
        rows = [
            {"game_id": 1, "livedata_id": "same", "mid": "m1"},
            {"game_id": 2, "livedata_id": "same", "mid": "m2"},
        ]
        mark_duplicates(rows)
        self.assertNotEqual(rows[0].get("reason"), "duplicate")
        self.assertEqual(rows[1]["reason"], "duplicate")
        self.assertIn("previously game 1", rows[1]["inconsistent_mapping"])


class WinnerLabelTest(unittest.TestCase):
    def _state(self):
        return {
            "teamsAlive": 1,
            "players": {
                "a": {"name": "A", "teamId": 13, "teamName": "Bellevue University", "teamPlacement": 1, "status": "dead"},
                "b": {"name": "B", "teamId": 4, "teamName": "Other", "teamPlacement": 2, "status": "eliminated"},
            },
        }

    def _board(self, team_id=13, name="Bellevue University"):
        return {
            "id": 123536,
            "teams": [
                {"teamId": 9, "name": "Not first", "overall_stats": {"teamPlacement": 2}},
                {
                    "teamId": team_id,
                    "name": name,
                    "overall_stats": {"teamPlacement": 1, "kills": 8, "damageDealt": 900},
                    "player_stats": [{"name": "A"}],
                },
            ],
        }

    def test_unique_first_place_must_match_the_replay_squad(self):
        winner = resolve_winner(self._state(), self._board())
        self.assertEqual(winner["teamId"], 13)
        self.assertEqual(winner["teamName"], "Bellevue University")
        self.assertIn("replay.player.teamPlacement", winner["label_source"])
        self.assertIn("teamPlacement", winner["label_source"])
        self.assertNotIn("kills", winner)
        self.assertNotIn("damageDealt", winner)

    def test_name_or_extra_first_place_keeps_outcome_unknown(self):
        self.assertIsNone(resolve_winner(self._state(), self._board(name="Wrong Name")))
        board = self._board()
        board["teams"].append(
            {"teamId": 3, "name": "Also first", "overall_stats": {"teamPlacement": 1}}
        )
        self.assertIsNone(resolve_winner(self._state(), board))

    def test_standings_alone_do_not_label_a_missing_replay_squad(self):
        state = {
            "teamsAlive": 1,
            "players": {
                "a": {"name": "A", "teamId": 13, "teamName": "Bellevue University", "status": "dead"},
            },
        }
        self.assertIsNone(resolve_winner(state, self._board()))

    def test_player_rows_do_not_carry_final_placement(self):
        state = {"players": self._state()["players"]}
        observed = {
            "players": {
                "a": {"name": 0, "teamId": 0, "teamName": 0, "teamPlacement": 0, "status": 0},
            }
        }
        rows = export_players(state, observed, 0)
        self.assertNotIn("teamPlacement", rows[0])


class ResumeTest(unittest.TestCase):
    def test_existing_raw_file_is_not_downloaded_again(self):
        class Boom:
            def get(self, url):
                raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw" / "abc.timeseries.json"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"{}")
            fetched = fetch_telemetry(
                {"livedata_id": "abc"},
                root,
                Boom(),
            )
        self.assertEqual(fetched["download_status"], "cached")
        self.assertEqual(fetched["sha256"], __import__("hashlib").sha256(b"{}").hexdigest())


class RateLimitTest(unittest.TestCase):
    def test_retry_after_seconds_and_http_date(self):
        from email.message import Message
        from datetime import datetime, timedelta, timezone

        from collect_matches import is_rate_limited, retry_after_seconds, TemporaryFailure

        numeric = Message()
        numeric["Retry-After"] = "120"
        exc = urllib.error.HTTPError("https://overstat.gg/api/stats/1/overall", 429, "Too Many Requests", numeric, None)
        self.assertEqual(retry_after_seconds(exc), 120.0)
        missing = urllib.error.HTTPError("https://overstat.gg/api/stats/1/overall", 429, "Too Many Requests", Message(), None)
        self.assertIsNone(retry_after_seconds(missing))
        dated = Message()
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        dated["Retry-After"] = email.utils.format_datetime(when, usegmt=True)
        dated_exc = urllib.error.HTTPError("https://overstat.gg/api/stats/1/overall", 429, "Too Many Requests", dated, None)
        self.assertAlmostEqual(retry_after_seconds(dated_exc), 30, delta=2)
        self.assertTrue(is_rate_limited(TemporaryFailure("https://overstat.gg/api/stats/1/overall: HTTP Error 429: Too Many Requests")))
        self.assertFalse(is_rate_limited(TemporaryFailure("timed out")))

    def test_429_honors_retry_after_once_then_stops(self):
        from email.message import Message
        from unittest import mock

        from collect_matches import Client, TemporaryFailure

        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            hdr = Message()
            hdr["Retry-After"] = "0"
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", hdr, None)

        client = Client(timeout=1, retries=6, sleep=lambda _seconds: None, delay=0)
        with mock.patch("collect_matches.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(TemporaryFailure):
                client.get("https://overstat.gg/api/stats/1/overall")
        self.assertEqual(len(calls), 2)

    def test_429_without_retry_after_does_not_walk_retries(self):
        from email.message import Message
        from unittest import mock

        from collect_matches import Client, TemporaryFailure

        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", Message(), None)

        client = Client(timeout=1, retries=6, sleep=lambda _seconds: None, delay=0)
        with mock.patch("collect_matches.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(TemporaryFailure):
                client.get("https://overstat.gg/api/stats/1/overall")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
