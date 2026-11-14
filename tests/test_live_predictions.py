"""Per-second predictions reproduce the frozen 30-second ones exactly."""

from __future__ import annotations

import unittest
from pathlib import Path

from dashboard_data import MatchStore
from live_predictions import FINAL, HELD_OUT, LivePredictions

DATA = Path(__file__).resolve().parents[1] / "data"
BUNDLE = DATA / "exports" / "heldout-session-split-v2" / "bundle.json"


@unittest.skipUnless(BUNDLE.is_file(), "v2 held-out bundle not exported")
class LivePredictionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = MatchStore(DATA.resolve())
        cls.live = LivePredictions(DATA.resolve())
        cls.game = min(cls.live.test_games)

    def test_held_out_seconds_equal_the_frozen_checkpoints(self):
        table = self.live.table(self.game, HELD_OUT)
        points = self.store.heldout(self.game)["points"]
        self.assertTrue(points)
        for point in points:
            mine = {str(s["teamId"]): s for s in table[point["t"]]["squads"]}
            for squad in point["squads"]:
                ours = mine[str(squad["teamId"])]
                self.assertEqual(ours["model"], squad["model"])
                self.assertEqual(ours["model_unconstrained"], squad["model_unconstrained"])

    def test_held_out_uses_the_bundle_model_not_the_final_model(self):
        provenance = self.live.model(HELD_OUT)["provenance"]
        self.assertEqual(provenance["file"], "exports/heldout-session-split-v2/evaluation_model.json")
        self.assertNotEqual(provenance["sha256"], self.live.model(FINAL)["provenance"]["sha256"])

    def test_every_second_until_the_cut_off_and_none_after(self):
        table = self.live.table(self.game, HELD_OUT)
        seconds = sorted(table)
        self.assertEqual(seconds, list(range(seconds[0], seconds[-1] + 1)))
        cutoff = self.live.at(self.game, seconds[-1] + 1, HELD_OUT)
        self.assertIsNone(cutoff["point"])
        self.assertEqual(cutoff["cutoff_t"], seconds[-1] + 1)

    def test_train_games_get_no_held_out_predictions(self):
        train = next(m["game_id"] for m in self.store.matches() if m["game_id"] not in self.live.test_games)
        self.assertFalse(self.live.at(train, 500, HELD_OUT)["available"])


if __name__ == "__main__":
    unittest.main()
