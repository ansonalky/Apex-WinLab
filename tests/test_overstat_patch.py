"""Patch operations the bundled jsondiffpatch decoder actually performs.

Expected values were checked against webpack module 3121 in
https://overstat.gg/js/8333.38781711.js.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from overstat_patch import (
    JS_UNDEFINED,
    PatchError,
    apply_frame,
    default_checkpoints,
    export_players,
    frame_entries,
    initial_state,
    patch,
    replay,
)


class PatchSemanticsTest(unittest.TestCase):
    def test_one_element_array_sets_the_new_value(self):
        state = patch({"feed": []}, {"state": ["preinit"], "n": [1]})
        self.assertEqual(state["state"], "preinit")
        self.assertEqual(state["n"], 1)
        self.assertEqual(state["feed"], [])

    def test_two_element_array_uses_the_new_value_not_a_merge(self):
        state = patch({"state": "preinit", "n": 1}, {"state": ["preinit", "Playing"], "n": [1, 2]})
        self.assertEqual(state, {"state": "Playing", "n": 2})

    def test_one_element_object_replaces_instead_of_merging(self):
        state = patch({"p": {"a": 1, "b": 2}}, {"p": [{"b": 9}]})
        self.assertEqual(state["p"], {"b": 9})

    def test_nested_delta_leaves_unmentioned_siblings(self):
        state = patch(
            {"p": {"pos": {"x": 1, "y": 2, "z": 3}, "name": "A"}},
            {"p": {"pos": {"y": [2, 9]}}},
        )
        self.assertEqual(state["p"]["pos"], {"x": 1, "y": 9, "z": 3})
        self.assertEqual(state["p"]["name"], "A")

    def test_three_element_zero_deletes_an_object_key(self):
        state = patch({"inv": {"gun": 1, "ammo": 2}}, {"inv": {"gun": [1, 0, 0]}})
        self.assertEqual(state["inv"], {"ammo": 2})
        self.assertNotIn("gun", state["inv"])

    def test_null_is_a_value_and_deletion_is_not_null(self):
        added = patch({"a": 1}, {"b": [None]})
        self.assertIn("b", added)
        self.assertIsNone(added["b"])
        replaced = patch({"a": 1}, {"a": [1, None]})
        self.assertIsNone(replaced["a"])
        deleted = patch({"a": 1, "b": 2}, {"a": [1, 0, 0]})
        self.assertNotIn("a", deleted)

    def test_array_insert_and_index_modification(self):
        state = patch({"arr": [10, 20]}, {"arr": {"_t": "a", "1": [25], "0": [10, 11]}})
        self.assertEqual(state["arr"], [11, 25, 20])

    def test_array_removal_keeps_later_items(self):
        state = patch({"a": ["x", "y", "z"]}, {"a": {"_t": "a", "_1": ["y", 0, 0]}})
        self.assertEqual(state["a"], ["x", "z"])

    def test_array_move(self):
        state = patch({"a": ["x", "y", "z"]}, {"a": {"_t": "a", "_0": ["x", 2, 3]}})
        self.assertEqual(state["a"], ["y", "z", "x"])

    def test_array_index_deletion_leaves_an_empty_slot(self):
        state = patch({"a": [1, 2, 3]}, {"a": {"_t": "a", "1": [2, 0, 0]}})
        self.assertEqual(len(state["a"]), 3)
        self.assertEqual(state["a"][0], 1)
        self.assertIs(state["a"][1], JS_UNDEFINED)
        self.assertEqual(state["a"][2], 3)

    def test_data_arrays_are_not_delta_arrays(self):
        state = patch({}, {"pair": [[1, 2]]})
        self.assertEqual(state["pair"], [1, 2])
        replaced = patch({"pair": [1, 2]}, {"pair": [[1, 2], [3, 4]]})
        self.assertEqual(replaced["pair"], [3, 4])

    def test_text_diff_is_rejected(self):
        with self.assertRaises(PatchError):
            patch({"note": "hello"}, {"note": ["@@ -1 +1 @@\n-hello\n+hi\n", 0, 2]})

    def test_empty_object_delta_does_not_change_state(self):
        state = {"a": 1, "feed": []}
        observed = {}
        new_state, new_observed, error = apply_frame(state, {}, [], observed, 0)
        self.assertEqual(error, "empty object delta")
        self.assertEqual(new_state, {"a": 1, "feed": []})
        self.assertEqual(new_observed, {})

    def test_failed_sibling_does_not_keep_an_earlier_replacement(self):
        state = {"a": 1}
        with self.assertRaises(PatchError):
            patch(state, {"a": [1, 2], "b": {}})
        self.assertEqual(state, {"a": 1})


class ReplaySemanticsTest(unittest.TestCase):
    def test_initial_state_matches_the_frontend(self):
        self.assertEqual(initial_state(), {"feed": []})

    def test_gap_frames_and_feed_follow_patch_data(self):
        series = [
            {"t": 0, "d": {"hp": [10]}, "f": [{"type": "damage"}]},
            {"t": 2, "d": {"hp": [10, 7]}, "f": []},
            {"t": 4, "d": {"hp": [7, 1]}, "f": []},
        ]
        frames = frame_entries(series)
        self.assertEqual([frame["t"] for frame in frames], [0, 1, 2, 3])
        self.assertNotIn("d", frames[1])
        result = replay(series, {0, 1})
        self.assertEqual(result["omitted_t"], 4)
        self.assertEqual(result["state"]["hp"], 7)
        self.assertEqual(result["state"]["feed"], [{"type": "damage"}])

    def test_carried_position_records_a_gap_and_missing_axis_stays_unknown(self):
        series = [
            {
                "t": 0,
                "d": {"players": [{"p": {"name": "A", "pos": {"x": 1, "y": 2}}}]},
                "f": [],
            },
            {"t": 2, "d": {"players": {"p": {"pos": {"y": [2, 9]}}}}, "f": []},
            {"t": 3, "d": {"ignored": [1]}, "f": []},
        ]
        result = replay(series, {2})
        row = result["snapshots"][2][0]
        self.assertEqual(row["pos"]["value"], {"x": 1, "y": 9, "z": None})
        self.assertEqual(row["pos"]["observed_at"], {"x": 0, "y": 2, "z": None})
        self.assertEqual(row["pos"]["gap"], {"x": 2, "y": 0, "z": None})
        self.assertEqual(row["status"], {"value": None, "observed_at": None, "gap": None})

    def test_later_patch_cannot_change_an_earlier_snapshot(self):
        series = [
            {"t": 0, "d": {"players": [{"p": {"name": "A", "pos": {"x": 1, "y": 2, "z": 3}, "currentHealth": 100}}]}, "f": []},
            {"t": 2, "d": {"players": {"p": {"pos": {"x": [1, 50]}, "currentHealth": [100, 10]}}}, "f": []},
            {"t": 3, "d": {"tail": [1]}, "f": []},
        ]
        result = replay(series, {0, 1})
        early = result["snapshots"][0]
        result["state"]["players"]["p"]["pos"]["x"] = 999
        result["state"]["players"]["p"]["currentHealth"] = 0
        self.assertEqual(early[0]["pos"]["value"]["x"], 1)
        self.assertEqual(early[0]["health"]["current"]["value"], 100)
        self.assertEqual(result["snapshots"][1][0]["pos"]["value"]["x"], 1)
        self.assertEqual(result["snapshots"][1][0]["health"]["current"]["gap"], 1)

    def test_snapshot_is_independent_of_a_later_full_replacement(self):
        state, observed, _error = apply_frame(
            initial_state(),
            {"players": [{"p": {"name": "A", "currentHealth": 80, "status": "alive"}}]},
            [],
            {},
            0,
        )
        early = export_players(state, observed, 0)
        state, observed, _error = apply_frame(
            state,
            {"players": {"p": [{"name": "A", "currentHealth": 5}]}},
            [],
            observed,
            4,
        )
        self.assertEqual(early[0]["status"]["value"], "alive")
        self.assertEqual(early[0]["health"]["current"]["value"], 80)
        replaced = export_players(state, observed, 4)
        self.assertIsNone(replaced[0]["status"]["value"])
        self.assertEqual(replaced[0]["health"]["current"]["value"], 5)
        self.assertEqual(replaced[0]["health"]["current"]["gap"], 0)

    def test_playing_checkpoints_are_start_middle_and_end(self):
        names = ["Prematch"] * 4 + ["Playing"] * 5 + ["WinnerDetermined"]
        self.assertEqual(default_checkpoints(names), [4, 6, 8])

    def test_replay_does_not_mutate_the_source_delta(self):
        series = [
            {"t": 0, "d": {"players": [{"p": {"pos": {"x": 1, "y": 2, "z": 3}}}]}},
            {"t": 1, "d": {"players": {"p": {"pos": {"x": [1, 8]}}}}},
            {"t": 2, "d": {"tail": [1]}},
        ]
        original = copy.deepcopy(series)
        replay(series, {1})
        self.assertEqual(series, original)


class SampleReplayTest(unittest.TestCase):
    """Values cross-checked against the bundled patcher on the downloaded sample."""

    SAMPLE = Path("data/raw/623ae07f-81cf-4634-ad7f-2d27f7911896.timeseries.json")
    # 180 player-rows at t=62, 639, and 1217. The 11 value fields matched the
    # bundled patcher field-for-field; gaps are part of this hash too.
    DIGEST = "8f715e003d270b2eeb146bc423879478ce6e65c8c8193faf6fa6159308cc5596"

    @unittest.skipUnless(SAMPLE.exists(), "sample payload is not downloaded")
    def test_sample_checkpoints_match_the_bundled_patcher(self):
        payload = json.loads(self.SAMPLE.read_text(encoding="utf-8-sig"))
        result = replay(payload["series"], {62, 639, 1217})
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["frame_count"], 1251)
        self.assertEqual(result["omitted_t"], 1251)
        self.assertEqual(default_checkpoints(result["state_names"]), [62, 639, 1217])
        rows = []
        for t in (62, 639, 1217):
            for player in sorted(result["snapshots"][t], key=lambda row: row["nucleusHash"]):
                rows.append(
                    [
                        t,
                        player["nucleusHash"],
                        player["name"]["value"],
                        player["teamId"]["value"],
                        player["pos"]["value"]["x"],
                        player["pos"]["value"]["y"],
                        player["pos"]["value"]["z"],
                        player["health"]["current"]["value"],
                        player["shields"]["current"]["value"],
                        player["status"]["value"],
                        player["pos"]["gap"]["x"],
                        player["pos"]["gap"]["y"],
                        player["pos"]["gap"]["z"],
                        player["status"]["gap"],
                    ]
                )
        digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, self.DIGEST)
        podge = next(
            row
            for row in result["snapshots"][62]
            if row["nucleusHash"] == "61234bac26d84f616124206113cb1a95"
        )
        self.assertEqual(podge["name"]["value"], "Podge")
        self.assertEqual(podge["pos"]["value"], {"x": -2640, "y": -21684, "z": 6377})
        self.assertEqual(podge["pos"]["gap"], {"x": 0, "y": 0, "z": 0})
        self.assertEqual(podge["health"]["current"]["value"], 100)
        self.assertEqual(podge["shields"]["current"]["value"], 50)
        self.assertEqual(podge["status"], {"value": "alive", "observed_at": 52, "gap": 10})


if __name__ == "__main__":
    unittest.main()
