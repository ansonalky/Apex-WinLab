import json
import unittest
from pathlib import Path

from ring_features import ring_at, ring_target

DATA = Path(__file__).resolve().parent.parent / "data"
GAME = 123633  # stages 0-5; stage 5 (ring 6) has no recorded end
CENTER = {"x": 0.0, "y": 0.0}
CLOSING = {"stage": 0, "start": 10, "end": 20, "startCenter": CENTER, "endCenter": {"x": 100.0, "y": 0.0},
           "startSize": 1000.0, "endSize": 500.0}
FINAL = {"stage": 1, "start": 30, "startCenter": CENTER, "startSize": 500.0, "endSize": 1}


class Target(unittest.TestCase):
    def test_target_only_while_a_recorded_stage_closes(self):
        self.assertEqual(ring_target([CLOSING], ring_at([CLOSING], 15)), {"x": 100.0, "y": 0.0, "r": 500.0, "stage": 0})
        self.assertIsNone(ring_target([CLOSING], ring_at([CLOSING], 25)))  # waiting: next stage not revealed
        self.assertIsNone(ring_target([CLOSING, FINAL], ring_at([CLOSING, FINAL], 35)))  # end not recorded
        self.assertIsNone(ring_target([], ring_at([], 5)))


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class OverlayOnRealReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tactical import TacticalIndex

        cls.index = TacticalIndex(DATA.resolve())
        cls.game = cls.index.game(GAME)
        cls.start = cls.game["playing_start"]

    def at(self, offset):
        return self.index.ring_overlay_at(GAME, self.start + offset)

    def test_before_the_first_closing_nothing_is_drawn(self):
        ring = self.at(60)
        self.assertEqual(ring["state"], "not_revealed")
        self.assertIsNone(ring["location"])
        self.assertIsNone(ring["target"])

    def test_mid_closing_interpolates_and_targets_the_revealed_end_circle(self):
        ring = self.at(250)  # ring 1 closes 125-385
        self.assertEqual((ring["state"], ring["label"]), ("closing", "Ring 1 closing"))
        expected = ring_at([self.live_entry(0)], self.start + 250)
        self.assertAlmostEqual(ring["location"]["r"], expected["radius"])
        entry = self.live_entry(0)
        self.assertEqual((ring["target"]["x"], ring["target"]["y"], ring["target"]["r"]),
                         (entry["endCenter"]["x"], entry["endCenter"]["y"], entry["endSize"]))
        self.assertGreater(ring["location"]["r"], ring["target"]["r"])
        self.assertEqual(ring["countdown_s"], 385 - 250)

    def test_after_closure_the_target_waits_for_the_next_reveal(self):
        closed = self.at(385)
        self.assertEqual((closed["state"], closed["label"]), ("waiting", "Ring 1 closed · ring 2 next"))
        self.assertIsNone(closed["target"])
        self.assertAlmostEqual(closed["location"]["r"], self.live_entry(0)["endSize"])
        self.assertIsNone(self.at(513)["target"])
        revealed = self.at(514)  # ring 2 starts closing; its entry is inserted now
        self.assertEqual(revealed["state"], "closing")
        self.assertEqual(revealed["target"]["r"], self.live_entry(1)["endSize"])

    def test_incomplete_final_ring_draws_nothing(self):
        ring = self.at(1110)
        self.assertEqual(ring["state"], "closing_target_unknown")
        self.assertEqual(ring["label"], "Ring 6 closing (end not recorded)")
        self.assertIsNone(ring["location"])
        self.assertIsNone(ring["target"])

    def test_backward_seek_matches_a_forward_read(self):
        forward = [self.at(offset) for offset in (100, 250, 385, 514, 1110)]
        backward = [self.at(offset) for offset in (1110, 514, 385, 250, 100)][::-1]
        self.assertEqual(forward, backward)

    def test_no_target_ever_while_waiting(self):
        for t, second in self.game["seconds"].items():
            if second["ring"].get("phase") != "closing":
                self.assertIsNone(second["ring_target"], t)

    def test_map_tactical_and_coach_labels_agree_and_coach_gets_no_target(self):
        from app import create_app
        from coach import build_packet

        client = create_app(DATA, backend="files").test_client()
        for offset in (60, 250, 385, 1110):
            t = self.start + offset
            frame = client.get(f"/api/matches/{GAME}/at/{t}").get_json()
            tactical = client.get(f"/api/matches/{GAME}/tactical?t={frame['t']}&team=11").get_json()
            self.assertEqual(frame["ring"]["label"], tactical["ring"]["label"], offset)
            self.assertNotIn("target", tactical["ring"])
            self.assertNotIn("ring_target", tactical["ring"])
        packet = client.get(f"/api/matches/{GAME}/coach/packet?t={self.start + 250}&team=11").get_json()
        self.assertNotIn("ring_target", json.dumps(packet))
        self.assertNotIn('"target":', json.dumps(packet))

    def live_entry(self, stage):
        from dashboard_data import _raw_path, _record
        from overstat_patch import apply_frame, frame_entries, initial_state
        from ring_features import live_entries

        series = json.loads(_raw_path(_record(DATA.resolve(), GAME), DATA.resolve()).read_text(encoding="utf-8-sig"))["series"]
        state, observed = initial_state(), {}
        for frame in frame_entries(series):
            state, observed, _ = apply_frame(state, frame.get("d"), frame.get("f") or [], observed, frame["t"])
        return next(entry for entry in live_entries(state, observed) if entry.get("stage") == stage)
