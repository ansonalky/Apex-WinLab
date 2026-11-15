"""Expansion policy. No network and no writes to a frozen manifest."""

import json
import tempfile
import unittest
from pathlib import Path

from expand_collection import (
    collect_expansion,
    frozen_index,
    mark_frozen_duplicate,
    preferred_maps,
    select_downloads,
    squad_format,
)


class PolicyTest(unittest.TestCase):
    def test_preferred_maps_are_the_two_largest_accepted_counts(self):
        frozen = {
            "games": {
                "1": {"reason": "accepted", "map_name": "mp_rr_tropic_island_mu2_landscape", "game_id": 1},
                "2": {"reason": "accepted", "map_name": "mp_rr_tropic_island_mu2_landscape", "game_id": 2},
                "3": {"reason": "accepted", "map_name": "mp_rr_district_mu1", "game_id": 3},
                "4": {"reason": "excluded", "map_name": "mp_rr_olympus_mu3", "game_id": 4},
                "5": {"reason": "accepted", "map_name": "mp_rr_desertlands_mu5", "game_id": 5},
            }
        }
        self.assertEqual(
            preferred_maps(frozen),
            ["mp_rr_tropic_island_mu2_landscape", "mp_rr_district_mu1"],
        )

    def test_frozen_game_is_a_duplicate_and_is_not_a_new_id(self):
        index = frozen_index(
            {
                "games": {
                    "9": {
                        "game_id": 9,
                        "session_id": 3,
                        "livedata_id": "live",
                        "mid": "mid",
                        "reason": "accepted",
                    }
                }
            }
        )
        record = {"game_id": 9, "livedata_id": "other", "mid": "other"}
        self.assertTrue(mark_frozen_duplicate(record, index))
        self.assertEqual(record["reason"], "duplicate")
        self.assertEqual(index["accepted"], 1)

    def test_preferred_maps_are_downloaded_before_other_maps(self):
        preferred = {"mp_rr_district_mu1", "mp_rr_tropic_island_mu2_landscape"}
        eligible = [
            {"game_id": 1, "map_name": "mp_rr_olympus_mu3"},
            {"game_id": 2, "map_name": "mp_rr_district_mu1"},
            {"game_id": 3, "map_name": "mp_rr_tropic_island_mu2_landscape"},
            {"game_id": 4, "map_name": "mp_rr_district_mu1"},
        ]
        chosen, skipped = select_downloads(eligible, preferred, per_session=2, other_slots=10)
        self.assertEqual([row["game_id"] for row in chosen], [2, 3])
        self.assertEqual(skipped[id(eligible[0])], "map_not_preferred")
        self.assertEqual(skipped[id(eligible[3])], "session_cap")

    def test_other_maps_are_used_only_when_the_session_has_no_preferred_candidate(self):
        eligible = [
            {"game_id": 1, "map_name": "mp_rr_olympus_mu3"},
            {"game_id": 2, "map_name": "mp_rr_desertlands_mu5"},
        ]
        chosen, skipped = select_downloads(eligible, {"mp_rr_district_mu1"}, per_session=2, other_slots=1)
        self.assertEqual([row["game_id"] for row in chosen], [1])
        self.assertEqual(skipped[id(eligible[1])], "session_cap")
        chosen, skipped = select_downloads(eligible, {"mp_rr_district_mu1"}, per_session=2, other_slots=0)
        self.assertEqual(chosen, [])
        self.assertEqual(skipped[id(eligible[0])], "other_map_cap")

    def test_format_follows_the_longest_roster(self):
        stats = {"teams": [{"player_stats": [{}, {}]}, {"player_stats": [{}, {}, {}]}]}
        self.assertEqual(squad_format(stats), "trios")
        self.assertEqual(squad_format({"teams": [{"player_stats": [{}]}]}), "solos")
        self.assertEqual(squad_format(None), "unknown")

    def test_expand_refuses_to_write_the_frozen_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            frozen.mkdir()
            (frozen / "collection").mkdir()
            (frozen / "collection" / "manifest.json").write_text(
                json.dumps({"version": 1, "games": {}}),
                encoding="utf-8",
            )
            before = (frozen / "collection" / "manifest.json").read_text(encoding="utf-8")
            with self.assertRaises(SystemExit):
                collect_expansion(frozen, frozen, request_budget=1, max_sessions=1)
            self.assertEqual((frozen / "collection" / "manifest.json").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
