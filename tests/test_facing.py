import json
import unittest
from pathlib import Path

from tactical import FacingTrack, _facing

DATA = Path(__file__).resolve().parent.parent / "data"
GAME = 123633


class FacingField(unittest.TestCase):
    def state(self, yaw, written_at):
        state = {"players": {"p1": {"nucleusHash": "h1", "angles": {"x": 0, "y": yaw, "z": 0}}}}
        observed = {"players": {"p1": {"angles": {"y": written_at}}}}
        return state, observed

    def test_yaw_and_age(self):
        state, observed = self.state(-178.374039, 100)
        self.assertEqual(_facing(state, observed, 103, {"p1"}), {"h1": {"yaw": -178.4, "age_s": 3}})

    def test_old_write_is_not_expired(self):
        state, observed = self.state(45.0, 100)
        self.assertEqual(_facing(state, observed, 400, {"p1"}), {"h1": {"yaw": 45.0, "age_s": 300}})

    def test_unconfirmed_yaw_is_dropped(self):
        state, observed = self.state(45.0, 100)
        self.assertEqual(_facing(state, observed, 100, set()), {})

    def test_missing_or_out_of_range_yaw_is_dropped(self):
        for yaw in (None, "north", 270.0, True):
            state, observed = self.state(yaw, 100)
            self.assertEqual(_facing(state, observed, 100, {"p1"}), {}, yaw)


class FacingTrackRules(unittest.TestCase):
    def player(self, status="alive", yaw=10.0):
        return {"status": status, "angles": {"y": yaw}}

    def run_frame(self, track, delta, state, before_state=None, error=None):
        before = FacingTrack.standing(before_state if before_state is not None else state)
        track.update(delta, error, before, state)

    def confirmed_track(self):
        track = FacingTrack()
        state = {"players": {"p1": self.player()}}
        self.run_frame(track, {"players": {"p1": {"angles": {"y": [5.0, 10.0]}}}}, state)
        self.assertEqual(track.confirmed, {"p1"})
        return track, state

    def test_snapshot_from_added_player_is_not_trusted(self):
        track = FacingTrack()
        state = {"players": {"p1": self.player()}}
        self.run_frame(track, {"players": {"p1": [self.player()]}}, state, before_state={"players": {}})
        self.assertEqual(track.confirmed, set())

    def test_held_across_frames_with_no_delta(self):
        track, state = self.confirmed_track()
        for _ in range(120):
            self.run_frame(track, None, state)
        self.assertEqual(track.confirmed, {"p1"})

    def test_other_leaves_do_not_invalidate(self):
        track, state = self.confirmed_track()
        self.run_frame(track, {"players": {"p1": {"pos": {"x": [1, 2]}}}}, state)
        self.assertEqual(track.confirmed, {"p1"})

    def test_yaw_added_removed_or_replaced_resets(self):
        for delta in (
            {"p1": {"angles": {"y": [10.0]}}},
            {"p1": {"angles": {"y": [10.0, 0, 0]}}},
            {"p1": {"angles": [{"y": 10.0}]}},
            {"p1": [self.player(), self.player()]},
            {"p1": [self.player(), 0, 0]},
        ):
            track, state = self.confirmed_track()
            self.run_frame(track, {"players": delta}, state)
            self.assertEqual(track.confirmed, set(), delta)

    def test_respawn_resets_unless_written_in_the_same_frame(self):
        track, _ = self.confirmed_track()
        dead = {"players": {"p1": self.player("dead")}}
        self.run_frame(track, {"players": {"p1": {"status": ["alive", "dead"]}}}, dead)
        alive = {"players": {"p1": self.player("alive")}}
        self.run_frame(track, {"players": {"p1": {"status": ["dead", "alive"]}}}, alive, before_state=dead)
        self.assertEqual(track.confirmed, set())

        track, _ = self.confirmed_track()
        self.run_frame(track, None, dead)
        self.run_frame(track, {"players": {"p1": {"status": ["dead", "alive"], "angles": {"y": [10.0, 20.0]}}}}, alive, before_state=dead)
        self.assertEqual(track.confirmed, {"p1"})

    def test_downed_and_revived_is_not_a_reset(self):
        track, _ = self.confirmed_track()
        downed = {"players": {"p1": self.player("downed")}}
        self.run_frame(track, {"players": {"p1": {"status": ["alive", "downed"]}}}, downed)
        alive = {"players": {"p1": self.player("alive")}}
        self.run_frame(track, {"players": {"p1": {"status": ["downed", "alive"]}}}, alive, before_state=downed)
        self.assertEqual(track.confirmed, {"p1"})

    def test_patch_error_or_replaced_players_clears(self):
        track, state = self.confirmed_track()
        self.run_frame(track, {"players": {"p1": {"angles": {"y": [10.0, 11.0]}}}}, state, error="rejected")
        self.assertEqual(track.confirmed, set())
        track, state = self.confirmed_track()
        self.run_frame(track, {"players": [{}, {"p1": self.player()}]}, state)
        self.assertEqual(track.confirmed, set())


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class FacingOnRealReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tactical import TacticalIndex

        cls.index = TacticalIndex(DATA.resolve())
        cls.game = cls.index.game(GAME)

    def test_values_match_an_independent_decoder_pass(self):
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
        got = {k: (v["yaw"], v["age_s"]) for k, v in self.index.facing_at(GAME, target)["players"].items()}
        self.assertTrue(got)
        self.assertLessEqual(set(got), set(expected))
        self.assertEqual(got, {k: expected[k] for k in got})

    def test_unchanged_yaw_is_held_past_five_seconds(self):
        ages = [
            v["age_s"]
            for second in self.game["seconds"].values()
            for v in second["facing"].values()
        ]
        self.assertTrue(any(age > 5 for age in ages))

    def test_no_value_is_carried_from_another_second(self):
        self.assertEqual(self.index.facing_at(GAME, -5)["players"], {})

    def test_frame_response_carries_facing_for_its_own_second(self):
        from app import create_app

        client = create_app(DATA, backend="files").test_client()
        t = self.game["playing_start"] + 855
        frame = client.get(f"/api/matches/{GAME}/at/{t}").get_json()
        self.assertNotIn("fresh_s", frame["facing"])
        ids = {player["id"] for player in frame["players"]}
        self.assertTrue(set(frame["facing"]["players"]) & ids)
        self.assertEqual(frame["facing"], self.index.facing_at(GAME, frame["t"]))
