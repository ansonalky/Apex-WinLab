"""Dashboard contract: one replay, no invented probabilities."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from dashboard_data import MatchStore, build_timeline, evaluation_panel, prediction_curve


def _series():
    player = {
        "name": "A",
        "teamId": 1,
        "teamName": "Red",
        "pos": {"x": 10, "y": 20, "z": 1},
        "currentHealth": 80,
        "maxHealth": 100,
        "shieldHealth": 25,
        "shieldMaxHealth": 50,
        "status": "alive",
        "nucleusHash": "a",
    }
    other = {
        "name": "B",
        "teamId": 2,
        "teamName": "Blue",
        "pos": {"x": 40, "y": 5, "z": 1},
        "currentHealth": 50,
        "maxHealth": 100,
        "shieldHealth": 0,
        "shieldMaxHealth": 50,
        "status": "downed",
        "nucleusHash": "b",
    }
    return [
        {"t": 0, "d": {"state": ["Playing"], "teamsAlive": [2], "players": [{"a": player, "b": other}]}, "f": []},
        {"t": 1, "d": {"players": {"a": {"pos": {"x": [10, 12]}}}}, "f": []},
        {"t": 2, "d": {"state": ["Playing", "WinnerDetermined"], "teamsAlive": [2, 1]}, "f": []},
    ]


class TimelineTest(unittest.TestCase):
    def test_one_pass_keeps_playing_frames_and_omits_placement(self):
        timeline = build_timeline(_series())
        self.assertEqual(timeline["playing_start"], 0)
        self.assertEqual([frame["elapsed"] for frame in timeline["frames"]], [0, 1])
        player = timeline["frames"][1]["players"][0]
        self.assertEqual(player["x"], 12)
        self.assertNotIn("teamPlacement", player)
        self.assertEqual(timeline["frames"][0]["players"][1]["status"], "downed")

    def test_missing_model_and_report_are_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            curve = prediction_curve([{"t": 0, "elapsed": 0, "rows": []}], data, 1)
            panel = evaluation_panel(data)
        self.assertFalse(curve["available"])
        self.assertEqual(curve["message"], "Not available")
        self.assertEqual(curve["prediction_type"], None)
        self.assertFalse(panel["available"])
        self.assertEqual(panel["message"], "Not available")


class ApiTest(unittest.TestCase):
    def test_selector_and_frame_use_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "collection").mkdir()
            (data / "raw").mkdir()
            raw = data / "raw" / "demo.timeseries.json"
            raw.write_text(json.dumps({"series": _series()}), encoding="utf-8")
            record = {
                "acceptance_status": "accepted",
                "game_id": 7,
                "session_id": 3,
                "event": "Demo Cup",
                "map_name": "mp_rr_district_mu1",
                "quality_score": 8.5,
                "match_start": 10,
                "livedata_id": "demo",
                "download_path": raw.as_posix(),
                "validation": {"winner": {"teamId": 1, "teamName": "Red", "label_source": "test"}},
            }
            (data / "collection" / "manifest.json").write_text(
                json.dumps({"games": {"7": record}}),
                encoding="utf-8",
            )
            app = create_app(data)
            client = app.test_client()
            matches = client.get("/api/matches").get_json()
            self.assertEqual(matches[0]["event"], "Demo Cup")
            self.assertEqual(matches[0]["quality_score"], 8.5)
            summary = client.get("/api/matches/7").get_json()
            self.assertGreaterEqual(summary["frame_count"], 2)
            self.assertIn("unverified", summary["coordinate_note"])
            self.assertFalse(summary["curve"]["available"])
            first = client.get("/api/matches/7/frames/0").get_json()
            second = client.get("/api/matches/7/frames/1").get_json()
            self.assertEqual(second["players"][0]["x"], 12)
            self.assertTrue((data / "dashboard" / "7.json").is_file())
            store = MatchStore(data)
            self.assertEqual(store.frame(7, 0)["t"], first["t"])
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Squad", page.data)
            evaluation = client.get("/api/evaluation").get_json()
            self.assertEqual(evaluation["message"], "Not available")


if __name__ == "__main__":
    unittest.main()
