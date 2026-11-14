"""File-backed Tiger row checks. No database connection."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tiger_rows import (
    FITTED,
    HELD_OUT,
    estimate_rows,
    frame_at,
    held_out_rows,
    load_timeline,
    player_count,
    player_tuples,
    replay_timestamp,
)


class RowContractTest(unittest.TestCase):
    def test_timestamp_uses_match_start_plus_replay_t(self):
        stamp = replay_timestamp(1_700_000_000, 100)
        self.assertEqual(stamp.timestamp(), 1_700_000_100)

    def test_missing_values_stay_none_and_reimport_keys_do_not_grow(self):
        record = {"game_id": 7, "match_start": 1_700_000_000}
        timeline = {
            "playing_start": 10,
            "frames": [
                {
                    "t": 10,
                    "elapsed": 0,
                    "players": [
                        {
                            "id": "a",
                            "name": "A",
                            "teamId": 1,
                            "teamName": "Red",
                            "x": None,
                            "y": 2,
                            "health": None,
                            "healthMax": 100,
                            "shields": None,
                            "shieldMax": None,
                            "status": None,
                        }
                    ],
                },
                {
                    "t": 11,
                    "elapsed": 1,
                    "players": [
                        {
                            "id": "a",
                            "name": "A",
                            "teamId": 1,
                            "teamName": "Red",
                            "x": 3,
                            "y": 4,
                            "health": 9,
                            "healthMax": 100,
                            "shields": 0,
                            "shieldMax": 50,
                            "status": "alive",
                        }
                    ],
                },
            ],
        }
        rows = player_tuples(record, timeline)
        self.assertIsNone(rows[0][7])
        self.assertIsNone(rows[0][9])
        self.assertIsNone(rows[0][13])
        self.assertEqual(frame_at(rows, 10)[0][2], 10)
        self.assertEqual(frame_at(rows, 100)[0][2], 11)
        keys = {(row[0], row[1], row[4]) for row in rows}
        self.assertEqual(len(keys | keys), len(keys))

    def test_held_out_is_not_filled_from_a_missing_bundle(self):
        root = Path(__file__).resolve().parents[1]
        data = root / "data"
        if not (data / "collection" / "manifest.json").is_file():
            self.skipTest("collection data is not on this machine")
        rows = estimate_rows(data, 123349)
        self.assertFalse(any(row[4] == HELD_OUT for row in rows))
        self.assertFalse(any(row[4] == FITTED for row in rows))
        self.assertEqual(held_out_rows(data, 123349), [])

    def test_real_cache_frame_matches_rows(self):
        data = Path(__file__).resolve().parents[1] / "data"
        cache = data / "dashboard" / "123349.json"
        if not cache.is_file():
            self.skipTest("dashboard cache for 123349 is not present")
        timeline = load_timeline(data, 123349)
        record = json.loads((data / "collection" / "manifest.json").read_text())
        record = next(row for row in record["games"].values() if row.get("game_id") == 123349)
        rows = player_tuples(record, timeline)
        self.assertEqual(len(rows), player_count(timeline))
        source = next(frame for frame in timeline["frames"] if frame["t"] == 130)
        loaded = frame_at(rows, 130)
        self.assertEqual({row[4] for row in loaded}, {player["id"] for player in source["players"]})
        self.assertEqual(loaded[0][0].timestamp(), record["match_start"] + 130)


if __name__ == "__main__":
    unittest.main()
