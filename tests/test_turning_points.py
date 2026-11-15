"""Turning-point windows, merges, and the recorded-outcome exclusion."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from turning_points import (
    LABEL,
    HighlightCache,
    detect_turning_points,
    highlights_for_game,
    predictions_from_table,
    recorded_events,
)

DATA = Path(__file__).resolve().parents[1] / "data"
PROVENANCE = {"sha256": "abc", "file": "models/model.json", "kind": "final_model_fitted"}


def row(t, probability, team=1, **extra):
    base = {
        "t": t,
        "elapsed": t - 100,
        "team_id": team,
        "team_name": "Alpha" if team == 1 else "Beta",
        "probability": probability,
        "kind": "final_model_fitted",
        "provenance": PROVENANCE,
    }
    base.update(extra)
    return base


def fill(start, end, probability, team=1, **extra):
    return [row(t, probability, team, **extra) for t in range(start, end + 1)]


class TurningPointTest(unittest.TestCase):
    def test_overlapping_windows_become_one_highlight(self):
        # Flat, then a step at t=20 that every 5/10/20s window covering it shares.
        predictions = fill(0, 19, 0.10) + fill(20, 40, 0.40)
        found = detect_turning_points(predictions, game_id=7, squad_id=1)
        self.assertEqual(found["label"], LABEL)
        self.assertEqual(len(found["up"]), 1)
        self.assertEqual(found["down"], [])
        highlight = found["up"][0]
        self.assertEqual(highlight["change_pp"], 30)
        self.assertEqual(highlight["window_s"], 5)
        self.assertEqual((highlight["start_t"], highlight["end_t"]), (15, 20))
        self.assertEqual((highlight["before"], highlight["after"]), (0.10, 0.40))
        self.assertGreater(highlight["windows_merged"], 1)
        self.assertLessEqual(highlight["episode_start_t"], highlight["start_t"])
        self.assertGreaterEqual(highlight["episode_end_t"], highlight["end_t"])
        self.assertEqual(highlight["episode_start_elapsed"], highlight["episode_start_t"] - 100)
        self.assertEqual(highlight["episode_end_elapsed"], highlight["episode_end_t"] - 100)
        self.assertEqual(highlight["seek"], {"game_id": 7, "t": 15, "elapsed": -85})
        self.assertIn("No knocks, revives, damage", highlight["description"])

    def test_separate_episodes_stay_separate_and_are_ordered(self):
        predictions = fill(0, 10, 0.10) + fill(11, 20, 0.30) + fill(40, 50, 0.30) + fill(51, 60, 0.80)
        found = detect_turning_points(predictions, squad_id=1, limit=3)
        self.assertEqual(len(found["up"]), 2)
        self.assertGreater(found["up"][0]["change_pp"], found["up"][1]["change_pp"])
        self.assertEqual(found["up"][0]["start_t"], 46)

    def test_missing_seconds_are_not_bridged(self):
        predictions = fill(0, 5, 0.20) + fill(7, 20, 0.80)
        found = detect_turning_points(predictions, squad_id=1)
        self.assertEqual(found["up"], [])
        gapped = [row(0, 0.10), row(10, 0.90)]
        self.assertEqual(detect_turning_points(gapped, squad_id=1)["up"], [])

    def test_recorded_outcome_jump_is_excluded(self):
        predictions = fill(0, 4, 0.20) + fill(5, 9, 0.50)
        predictions.append(row(10, 1.0, recorded_outcome=True))
        found = detect_turning_points(predictions, squad_id=1, conclusion_t=10)
        self.assertEqual(len(found["up"]), 1)
        self.assertEqual(found["up"][0]["end_t"], 5)
        self.assertEqual(found["up"][0]["after"], 0.50)
        self.assertNotEqual(found["up"][0]["after"], 1.0)

    def test_downward_change_and_squad_events_are_separated(self):
        predictions = fill(100, 110, 0.60) + fill(111, 120, 0.20)
        events = [
            {"t": 111, "type": "knock", "actor_team_id": 1, "victim_team_id": 4, "actor_name": "A", "victim_name": "V"},
            {"t": 108, "type": "squad_elimination", "actor_team_id": None, "victim_team_id": 9, "ambiguous": False},
            {"t": 105, "type": "damage", "actor_team_id": 4, "victim_team_id": 1},
            {"t": 105, "type": "damage", "actor_team_id": 2, "victim_team_id": 3},
            {"t": 50, "type": "revive", "actor_team_id": 1, "victim_team_id": None},
        ]
        found = detect_turning_points(predictions, events, squad_id=1, windows=(10,))
        highlight = found["down"][0]
        self.assertAlmostEqual(highlight["change_pp"], -40)
        kinds = {event["type"]: event["side"] for event in highlight["events"]}
        self.assertEqual(kinds["knock"], "squad")
        self.assertEqual(kinds["squad_elimination"], "opponent")
        self.assertNotIn("revive", kinds)
        self.assertEqual(highlight["damage"], {"packets": 2, "by_squad": 0, "against_squad": 1, "other": 1})
        self.assertNotIn("caused", highlight["description"].lower())
        self.assertIn("no recorded action", highlight["description"])

    def test_kinds_and_versions_are_not_mixed(self):
        mixed = fill(0, 10, 0.2) + [row(t, 0.2, kind="held_out") for t in range(0, 11)]
        with self.assertRaises(ValueError):
            detect_turning_points(mixed)
        changed = fill(0, 10, 0.2)
        changed[-1] = row(10, 0.2, provenance={"sha256": "other", "file": "models/model.json"})
        with self.assertRaises(ValueError):
            detect_turning_points(changed)

    def test_table_adapter_uses_the_constrained_probability(self):
        table = {
            5: {
                "t": 5,
                "elapsed": 0,
                "squads": [
                    {"teamId": 1, "team_name": "Alpha", "model": 0.25, "model_unconstrained": 0.9},
                ],
            }
        }
        rows = predictions_from_table(table, kind="held_out", provenance=PROVENANCE)
        self.assertEqual(rows[0]["probability"], 0.25)
        self.assertEqual(rows[0]["kind"], "held_out")

    def test_real_feed_events_keep_the_frame_clock(self):
        manifest = DATA / "collection" / "manifest.json"
        if not manifest.is_file():
            self.skipTest("telemetry is not on this machine")
        document = json.loads(manifest.read_text(encoding="utf-8"))
        record = next(row for row in document["games"].values() if row.get("game_id") == 123349)
        payload = json.loads((DATA.parent / record["download_path"]).read_text(encoding="utf-8-sig"))
        events = recorded_events(payload["series"], playing_start=100)
        counts = {}
        for event in events:
            counts[event["type"]] = counts.get(event["type"], 0) + 1
            self.assertEqual(event["elapsed"], event["t"] - 100)
        self.assertEqual(counts["knock"], 117)
        self.assertEqual(counts["squad_elimination"], 19)
        self.assertEqual(counts["revive"], 24)
        self.assertEqual(counts["respawn"], 21)
        self.assertNotIn("kill", counts)
        elimination = next(event for event in events if event["type"] == "squad_elimination")
        self.assertIsNotNone(elimination["victim_team_id"])

    def test_missing_model_does_not_invent_highlights(self):
        if not (DATA / "collection" / "manifest.json").is_file():
            self.skipTest("telemetry is not on this machine")
        found = highlights_for_game(DATA, 123349, "final_model_fitted")
        if found["available"]:
            self.assertEqual(found["label"], LABEL)
            for highlight in found["up"] + found["down"]:
                self.assertLess(highlight["end_t"], found["cutoff_t"])
                self.assertLess(highlight["after"], 1.0)
        else:
            self.assertTrue(any("model.json" in item for item in found["missing"]))
            self.assertEqual(found["up"], [])
            self.assertEqual(found["down"], [])


class _Versioned:
    def __init__(self, version):
        self.version = version
        self.tables = 0

    def model(self, kind):
        if self.version is None:
            return None
        return {"provenance": {"sha256": self.version, "kind": kind}}

    def table(self, game_id, kind):
        self.tables += 1
        return {}


class HighlightCacheTest(unittest.TestCase):
    def test_key_is_game_kind_squad_and_model_version(self):
        calls = []

        def compute(data_dir, game_id, kind, squad_id=None, **kwargs):
            live = kwargs["live"]
            live.table(game_id, kind)
            calls.append((kind, squad_id, live.version))
            return {"available": True, "n": len(calls)}

        live = _Versioned("abc")
        cache = HighlightCache()
        with patch("turning_points.highlights_for_game", side_effect=compute):
            first = cache.get(DATA, 123633, "held_out", "20", live=live)
            second = cache.get(DATA, 123633, "held_out", 20, live=live)
            other_kind = cache.get(DATA, 123633, "final_model_fitted", 20, live=live)
            live.version = "def"
            other_version = cache.get(DATA, 123633, "held_out", 20, live=live)
        self.assertIs(first, second)
        self.assertEqual(live.tables, 3)
        self.assertEqual(calls, [
            ("held_out", 20, "abc"),
            ("final_model_fitted", 20, "abc"),
            ("held_out", 20, "def"),
        ])
        self.assertIsNot(other_kind, first)
        self.assertIsNot(other_version, first)


class EqtHighlightTest(unittest.TestCase):
    def test_down_window_excludes_the_knock_and_the_recorded_result(self):
        model = DATA / "models" / "model.json"
        heldout_model = DATA / "exports" / "heldout-session-split-v2" / "evaluation_model.json"
        if not model.is_file() or not heldout_model.is_file():
            self.skipTest("frozen models are not on this machine")
        from live_predictions import FINAL, HELD_OUT, LivePredictions

        live = LivePredictions(DATA)
        counts = []
        real_table = live.table

        def counting(game_id, kind):
            counts.append((game_id, kind))
            return real_table(game_id, kind)

        live.table = counting
        cache = HighlightCache()
        held = cache.get(DATA, 123633, HELD_OUT, "20", live=live)
        again = cache.get(DATA, 123633, HELD_OUT, 20, live=live)
        final = cache.get(DATA, 123633, FINAL, 20, live=live)
        self.assertIs(held, again)
        # table() once for the list and once for the cut-off probe, per kind.
        # The repeated held-out request is the cached list, not a new table.
        self.assertEqual(counts, [
            (123633, HELD_OUT),
            (123633, HELD_OUT),
            (123633, FINAL),
            (123633, FINAL),
        ])
        self.assertTrue(held["available"])
        self.assertTrue(final["available"])
        self.assertNotEqual(held["provenance"]["sha256"], final["provenance"]["sha256"])
        down = held["down"][0]
        self.assertEqual(down["squad_name"], "EQT")
        self.assertEqual((down["start_elapsed"], down["end_elapsed"]), (1093, 1113))
        self.assertEqual((down["episode_start_elapsed"], down["episode_end_elapsed"]), (1085, 1129))
        self.assertEqual(down["windows_merged"], 46)
        self.assertFalse(down["start_t"] <= 1197 <= down["end_t"])
        self.assertTrue(down["episode_start_t"] <= 1197 <= down["episode_end_t"])
        self.assertNotIn(1197, [event["t"] for event in down["events"]])
        self.assertEqual(down["prediction_kind"], HELD_OUT)
        final_down = final["down"][0]
        self.assertEqual((final_down["start_elapsed"], final_down["end_elapsed"]), (1093, 1113))
        self.assertFalse(final_down["start_t"] <= 1197 <= final_down["end_t"])
        self.assertEqual(final_down["prediction_kind"], FINAL)
        for result in (held, final):
            for item in result["up"] + result["down"]:
                self.assertLess(item["end_t"], result["cutoff_t"])
                self.assertLess(item["after"], 1.0)
                self.assertNotEqual(item["seek"]["t"], 1197)
                self.assertEqual(item["prediction_kind"], result["kind"])


if __name__ == "__main__":
    unittest.main()
