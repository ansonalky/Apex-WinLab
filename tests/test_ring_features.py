"""Ring semantics: revealed entries only, linear closing, explicit unknowns."""

from __future__ import annotations

import unittest

from ring_features import NEARBY_RING_RADII, live_entries, ring_at, ring_snapshots, squad_ring_features
from overstat_patch import apply_frame, initial_state


def stage(n, start, end, sc, ec, ss, es):
    return {"stage": n, "start": start, "end": end, "startCenter": {"x": sc[0], "y": sc[1], "z": 0},
            "endCenter": {"x": ec[0], "y": ec[1], "z": 0}, "startSize": ss, "endSize": es}


PLAN = [stage(0, 10, 20, (0, 0), (100, 0), 1000, 500), stage(1, 30, 40, (100, 0), (9999, 9999), 500, 100)]


def player(team, x, y, status="alive"):
    return {"name": f"p{team}{x}", "teamId": team, "teamName": f"T{team}", "pos": {"x": x, "y": y, "z": 0},
            "status": status, "currentHealth": 100, "maxHealth": 100, "shieldHealth": 0, "shieldMaxHealth": 50}


def series():
    players = {"a": player(1, 0, 0), "b": player(1, 10, 0), "c": player(2, 900, 0), "d": player(3, 50, 0, "eliminated")}
    return [
        {"t": 0, "d": {"state": ["Playing"], "players": [players], "rings": [[dict(s) for s in PLAN]]}},
        {"t": 10, "d": {"rings": {"_t": "a", "0": [PLAN[0]]}}},
        {"t": 30, "d": {"rings": {"_t": "a", "1": [{k: v for k, v in PLAN[1].items() if k not in ("end", "endCenter")}]}}},
        {"t": 50, "d": {"tail": [1]}},
    ]


class RingAtTest(unittest.TestCase):
    def test_nothing_revealed_is_unknown(self):
        self.assertEqual(ring_at([], 5)["phase"], "not_revealed")
        self.assertFalse(ring_at([], 5)["known"])

    def test_closing_is_linear_and_waiting_is_the_end_circle(self):
        mid = ring_at([PLAN[0]], 15)
        self.assertEqual(mid["phase"], "closing")
        self.assertEqual(mid["center"], (50.0, 0.0))
        self.assertEqual(mid["radius"], 750.0)
        self.assertEqual(mid["closing_remaining_s"], 5)
        after = ring_at([PLAN[0]], 25)
        self.assertEqual((after["phase"], after["center"], after["radius"]), ("waiting", (100, 0), 500))
        self.assertIsNone(after["closing_remaining_s"])

    def test_missing_target_is_unknown_not_guessed(self):
        entry = {k: v for k, v in PLAN[1].items() if k not in ("end", "endCenter")}
        ring = ring_at([PLAN[0], entry], 35)
        self.assertFalse(ring["known"])
        self.assertEqual(ring["phase"], "closing_target_unknown")


class RevealTest(unittest.TestCase):
    def test_the_t0_plan_is_never_used(self):
        state, observed = initial_state(), {}
        for frame in series()[:1]:
            state, observed, _ = apply_frame(state, frame["d"], [], observed, frame["t"])
        self.assertEqual(len(state["rings"]), 2)
        self.assertEqual(live_entries(state, observed), [])

    def test_snapshots_follow_live_inserts(self):
        snaps = ring_snapshots(series(), {5, 15, 25, 35})
        self.assertEqual(snaps[5]["ring"]["phase"], "not_revealed")
        self.assertEqual(snaps[15]["ring"]["phase"], "closing")
        self.assertEqual(snaps[25]["ring"]["phase"], "waiting")
        # Stage 1's future target (9999, 9999) is in the t0 plan but never revealed live.
        self.assertEqual(snaps[35]["ring"]["phase"], "closing_target_unknown")
        self.assertIsNone(snaps[35]["squads"][1]["ring_center_dist_rel"])
        self.assertEqual(snaps[35]["squads"][1]["ring_geometry_missing"], 1.0)


class SquadFeatureTest(unittest.TestCase):
    def test_ring_relative_values_and_unknowns(self):
        snaps = ring_snapshots(series(), {25})
        squads = snaps[25]["squads"]
        # Ring at t 25: center (100, 0), radius 500. Squad 2 stands at x 900: outside by 300.
        self.assertEqual(squads[2]["ring_outside_frac"], 1.0)
        self.assertAlmostEqual(squads[2]["ring_boundary_signed_rel"], 0.6)
        self.assertEqual(squads[1]["ring_outside_frac"], 0.0)
        self.assertAlmostEqual(squads[1]["ring_spread_rel"], 5 / 500)
        # Squad 3 has no standing player: everything positional is unknown.
        self.assertIsNone(squads[3]["ring_center_dist_rel"])
        self.assertEqual(squads[3]["ring_squad_position_missing"], 1.0)
        self.assertEqual(squads[2]["ring_outside_frac_closing"], 0.0)  # waiting, not closing

    def test_nearby_threshold_is_ring_relative(self):
        ring = {"known": True, "phase": "closing", "center": (0.0, 0.0), "radius": 1000.0}

        def row(team, x):
            leaf = lambda v: {"value": v, "observed_at": 0, "gap": 0}
            return {"teamId": leaf(team), "status": leaf("alive"),
                    "pos": {"value": {"x": x, "y": 0.0, "z": 0}, "observed_at": {}, "gap": {}}}

        inside = NEARBY_RING_RADII * 1000 - 1
        squads = squad_ring_features([row(1, 0.0), row(2, inside), row(3, inside + 10)], ring)
        self.assertEqual(squads[1]["ring_nearby_enemies"], 1.0)
        self.assertAlmostEqual(squads[1]["ring_nearest_enemy_rel"], inside / 1000)
        self.assertEqual(squads[1]["ring_outside_frac_closing"], 0.0)


if __name__ == "__main__":
    unittest.main()
