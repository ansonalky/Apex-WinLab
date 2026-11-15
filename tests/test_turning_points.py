"""Turning-point windows, merges, and the recorded-outcome exclusion."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from turning_points import LABEL, detect_turning_points, highlights_for_game, predictions_from_table, recorded_events

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


if __name__ == "__main__":
    unittest.main()
