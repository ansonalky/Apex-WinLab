"""Tactical measurements use only data at or before t, and keep stale data out."""

from __future__ import annotations

import unittest

from tactical import FIRE_WINDOW_S, _damage, bearing, measure


def _p(name, team, status, x, y, z, age=0):
    return {"name": name, "team": team, "status": status, "x": x, "y": y, "z": z, "pos_age_s": age, "z_age_s": age}


def _game(players, damage, ring=None):
    return {
        "playing_start": 0,
        "seconds": {100: {"players": players, "ring": ring or {"known": False, "phase": "not_revealed"}, "phase": "Playing"}},
        "damage": damage,
    }


def _hit(t, attacker_team, victim_team, ax, ay, vx, vy, amount, weapon="R-301"):
    event = {
        "type": "damage",
        "player": {"teamId": attacker_team, "teamName": f"T{attacker_team}", "pos": {"x": ax, "y": ay}} if attacker_team else {"name": "World", "teamId": 0},
        "victim": {"teamId": victim_team, "name": "v", "pos": {"x": vx, "y": vy}},
        "damage": amount,
        "weapon": weapon,
    }
    return _damage(t, event)


class TacticalTest(unittest.TestCase):
    def test_bearing_is_clockwise_from_map_north(self):
        self.assertEqual(bearing(0, 1), (0.0, "N"))
        self.assertEqual(bearing(1, 0), (90.0, "E"))
        self.assertEqual(bearing(0, -1), (180.0, "S"))
        self.assertEqual(bearing(-1, 0), (270.0, "W"))

    def test_fire_uses_only_the_window_up_to_t_and_splits_the_ring(self):
        damage = [
            _hit(100, 2, 1, 0, 1000, 0, 0, 30),                    # in window, from the north
            _hit(100 - FIRE_WINDOW_S + 1, 3, 1, 1000, 0, 0, 0, 20),  # oldest second in window, from the east
            _hit(100 - FIRE_WINDOW_S, 2, 1, 0, 1000, 0, 0, 99),    # just outside the window
            _hit(101, 2, 1, 0, 1000, 0, 0, 99),                    # after t: never used
            _hit(99, None, 1, None, None, 0, 0, 5, weapon="The Ring"),
        ]
        players = [_p("a", 1, "alive", 0, 0, 10), _p("b", 2, "alive", 0, 1000, 0)]
        fire = measure(_game(players, damage), 100, 1)["fire"]
        self.assertEqual(fire["damage_taken"], 55)
        self.assertEqual(fire["from_opponents"], 50)
        self.assertEqual(fire["from_ring"], 5)
        self.assertEqual(fire["attacking_squads"], 2)
        self.assertEqual({a["team"]: a["sector"] for a in fire["attackers"]}, {"2": "N", "3": "E"})

    def test_stale_and_eliminated_positions_are_not_used(self):
        players = [
            _p("me", 1, "alive", 0, 0, 100),
            _p("gone", 1, "eliminated", 5000, 5000, 900),
            _p("fresh", 2, "alive", 1000, 0, 0),
            _p("stale", 3, "alive", 0, 1500, 0, age=40),
            _p("dead", 4, "eliminated", 0, -1000, 0),
        ]
        result = measure(_game(players, []), 100, 1)
        self.assertEqual([o["team"] for o in result["pressure"]["opponents"]], ["2"])
        self.assertEqual(result["quality"]["opposing_standing_stale_excluded"], 1)
        self.assertEqual(result["elevation"]["squad_z"], 100)
        self.assertEqual(result["elevation"]["mean_dz"], 100)
        gone = next(m for m in result["quality"]["members"] if m["name"] == "gone")
        self.assertFalse(gone["position_used"])

    def test_close_squads_get_no_bearing(self):
        players = [_p("me", 1, "alive", 0, 0, 0), _p("them", 2, "alive", 100, 0, 0)]
        pressure = measure(_game(players, []), 100, 1)["pressure"]
        self.assertIsNone(pressure["opponents"][0]["sector"])
        self.assertEqual(pressure["too_close_for_bearing"], 1)

    def test_unrevealed_ring_is_not_measured(self):
        players = [_p("me", 1, "alive", 0, 0, 0)]
        ring = measure(_game(players, []), 100, 1)["ring"]
        self.assertFalse(ring["known"])
        self.assertNotIn("boundary_distance", ring)


if __name__ == "__main__":
    unittest.main()
