"""Corrected combat health and the development-game filter. No frozen-model edit."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import train_model
from tactical_experiment import development_records
from tactical_features import CORRECTED_CONDITION, ELEVATION, FEATURE_SETS, REPRESENTATION, columns_from_measure, corrected_health


def _player(status, health, health_max, shield, shield_max, team=1):
    def box(value):
        return {"value": value}

    return {
        "teamId": box(team),
        "status": box(status),
        "health": {"current": box(health), "max": box(health_max)},
        "shields": {"current": box(shield), "max": box(shield_max)},
    }


class CorrectedHealthTest(unittest.TestCase):
    def test_downed_bleed_does_not_enter_standing_combat_health(self):
        snapshot = [
            _player("alive", 10, 100, 0, 100),
            _player("downed", 100, 100, 0, 0),
        ]
        health = corrected_health(snapshot, 1)
        self.assertAlmostEqual(health["standing_hp_frac"], 10 / 200)
        self.assertEqual(health["standing_hp_missing"], 0.0)
        self.assertAlmostEqual(health["downed_bleed_frac"], 1.0)
        mixed_now = 10 + 0 + 100
        mixed_cap = 100 + 100 + 100
        self.assertNotAlmostEqual(health["standing_hp_frac"], mixed_now / mixed_cap)

    def test_missing_measurement_stays_missing(self):
        columns = columns_from_measure({"available": False})
        self.assertIsNone(columns["elev_mean_dz"])
        self.assertEqual(columns["elev_missing"], 1.0)
        self.assertIsNone(columns["ring_outside_frac"])
        self.assertEqual(columns["ring_access_missing"], 1.0)

    def test_feature_sets_share_the_corrected_condition_columns(self):
        for name, features in FEATURE_SETS.items():
            self.assertEqual(features[: len(CORRECTED_CONDITION)], CORRECTED_CONDITION)
            self.assertNotIn("hp_frac", features)
        self.assertIn("multiplied", REPRESENTATION)
        self.assertIn("cancels", REPRESENTATION)

    def test_frozen_test_games_are_excluded(self):
        config = json.loads(Path("config/frozen_evaluation.json").read_text(encoding="utf-8"))
        records = [
            {"game_id": config["games"]["test"][0], "session_id": config["sessions"]["test"][0]},
            {"game_id": config["games"]["train"][0], "session_id": config["sessions"]["train"][0]},
        ]
        kept = development_records(records, config)
        self.assertEqual([record["game_id"] for record in kept], [config["games"]["train"][0]])

    def test_elevation_weight_is_not_forced_positive(self):
        names = CORRECTED_CONDITION + ELEVATION

        def row(team, elevation):
            features = {name: 0.0 for name in names}
            features["players_alive"] = 1.0
            features["elev_mean_dz"] = elevation
            return {"team": team, "features": features, "eliminated": False}

        groups = []
        for index in range(12):
            groups.append(
                {
                    "game_id": index,
                    "session_id": index,
                    "t": 0,
                    "rows": [row(1, 100.0), row(2, -100.0)],
                    "label": 1,
                    "elimination_verified": True,
                }
            )
        model = train_model.fit(groups, l2=0.01, features=names)
        weight = model["weights"][list(names).index("elev_mean_dz")]
        self.assertLess(weight, 0.0)
        self.assertTrue(model["optimizer"]["converged"])


if __name__ == "__main__":
    unittest.main()
