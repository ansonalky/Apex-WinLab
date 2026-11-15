import json
import unittest
from pathlib import Path

from tactical import FACING_FRESH_S, _facing

DATA = Path(__file__).resolve().parent.parent / "data"
GAME = 123633


class FacingField(unittest.TestCase):
    def state(self, yaw, written_at):
        state = {"players": {"p1": {"nucleusHash": "h1", "angles": {"x": 0, "y": yaw, "z": 0}}}}
        observed = {"players": {"p1": {"angles": {"y": written_at}}}}
        return state, observed

    def test_yaw_and_age(self):
        state, observed = self.state(-178.374039, 100)
        self.assertEqual(_facing(state, observed, 103), {"h1": {"yaw": -178.4, "age_s": 3}})

    def test_missing_or_out_of_range_yaw_is_dropped(self):
        for yaw in (None, "north", 270.0, True):
            state, observed = self.state(yaw, 100)
            self.assertEqual(_facing(state, observed, 100), {}, yaw)

    def test_stale_threshold_matches_positions(self):
        from tactical import FRESH_GAP_S
        self.assertEqual(FACING_FRESH_S, FRESH_GAP_S)


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class FacingOnRealReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tactical import TacticalIndex

        cls.index = TacticalIndex(DATA.resolve())
        cls.game = cls.index.game(GAME)

    def test_exact_second_matches_an_independent_decoder_pass(self):
        from dashboard_data import _raw_path, _record
        from overstat_patch import _leaf, apply_frame, frame_entries, initial_state

        target = self.game["playing_start"] + 855  # 14:15
        record = _record(DATA.resolve(), GAME)
        series = json.loads(_raw_path(record, DATA.resolve()).read_text(encoding="utf-8-sig"))["series"]
        state, observed = initial_state(), {}
        for frame in frame_entries(series):
            if frame["t"] > target:
                break
            events = frame.get("f") or []
            state, observed, _ = apply_frame(state, frame.get("d"), events if isinstance(events, list) else [], observed, frame["t"])
        expected = {}
        for pid, player in state["players"].items():
            leaf = _leaf(player, observed["players"].get(pid, {}), target, "angles", "y")
            if isinstance(leaf["value"], (int, float)):
                expected[player.get("nucleusHash", pid)] = (round(leaf["value"], 1), leaf["gap"])
        got = self.index.facing_at(GAME, target)["players"]
        self.assertTrue(expected)
        self.assertEqual({k: (v["yaw"], v["age_s"]) for k, v in got.items()}, expected)

    def test_no_value_is_carried_from_another_second(self):
        self.assertEqual(self.index.facing_at(GAME, -5)["players"], {})

    def test_frame_response_carries_facing_for_its_own_second(self):
        from app import create_app

        client = create_app(DATA, backend="files").test_client()
        t = self.game["playing_start"] + 855
        frame = client.get(f"/api/matches/{GAME}/at/{t}").get_json()
        self.assertEqual(frame["facing"]["fresh_s"], FACING_FRESH_S)
        ids = {player["id"] for player in frame["players"]}
        self.assertTrue(set(frame["facing"]["players"]) & ids)
        self.assertEqual(frame["facing"], self.index.facing_at(GAME, frame["t"]))
