"""Training refuses thin data, converges, and evaluates only on held-out data.

Synthetic data here checks mechanics and the estimator. It is not evidence
of model performance on real matches.
"""

from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import train_model
from features import FEATURES
from tests.test_features import synthetic_game, write_dataset


def group(game_id, label, features_by_squad, session_id=1, elapsed=0):
    rows = []
    for values in features_by_squad:
        features = {name: 0.0 for name in FEATURES}
        features.update(values)
        rows.append({"team": len(rows), "features": features})
    return {"game_id": game_id, "session_id": session_id, "t": elapsed, "elapsed": elapsed, "rows": rows, "label": label}


def simulated(true_weight: float, games: int, seed: int = 7, session_of=lambda g: 1):
    """Groups drawn from a known conditional logit on one feature."""
    rng = random.Random(seed)
    groups = []
    for g in range(games):
        alive = [float(rng.randint(0, 3)) for _ in range(4)]
        probs = train_model.softmax([true_weight * a for a in alive])
        label = rng.choices(range(4), weights=probs)[0]
        groups.append(group(g, label, [{"players_alive": a} for a in alive], session_id=session_of(g)))
    return groups


class InsufficientDataTest(unittest.TestCase):
    def test_no_manifest_reports_and_writes_no_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "models"
            report = train_model.run(Path(tmp) / "data", out, 20)
            self.assertEqual(report["status"], "insufficient_data")
            self.assertIsNone(report["evaluation"])
            self.assertFalse((out / "model.json").exists())

    def test_below_min_games_does_not_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            write_dataset(data, [{"game_id": g, "session_id": 1, "winner": 1, "payload": synthetic_game()} for g in range(4)])
            with mock.patch.object(train_model, "fit") as fit:
                report = train_model.run(data, Path(tmp) / "models", 5)
            fit.assert_not_called()
            self.assertEqual(report["status"], "insufficient_data")
            self.assertEqual(report["inventory"]["usable"], 4)

    def test_min_games_has_a_floor(self):
        with self.assertRaises(SystemExit):
            train_model.run(Path("nowhere"), Path("nowhere"), train_model.MIN_GAMES_FLOOR - 1)

    def test_cli_exit_code_is_nonzero_without_data(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("builtins.print"):
            code = train_model.main(["--data-dir", f"{tmp}/data", "--out-dir", f"{tmp}/models"])
            report = json.loads((Path(tmp) / "models" / "report.json").read_text())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "insufficient_data")


class OptimizerTest(unittest.TestCase):
    def _data(self, groups):
        scaler = train_model.fit_scaler(groups)
        data = [([train_model.vectorize(r, scaler) for r in g["rows"]], g["label"]) for g in groups]
        return data, train_model.game_weights(groups)

    def test_gradient_and_hessian_match_finite_differences(self):
        data, weights = self._data(simulated(1.0, 30))
        rng = random.Random(1)
        beta = [rng.uniform(-0.5, 0.5) for _ in FEATURES]
        _, grad, hess = train_model.objective(beta, data, weights, 0.1, hessian=True)
        h = 1e-6
        for j in range(len(beta)):
            up = beta[:]
            down = beta[:]
            up[j] += h
            down[j] -= h
            numeric = (train_model.objective(up, data, weights, 0.1)[0] - train_model.objective(down, data, weights, 0.1)[0]) / (2 * h)
            self.assertAlmostEqual(grad[j], numeric, places=5)
            g_up = train_model.objective(up, data, weights, 0.1)[1]
            g_down = train_model.objective(down, data, weights, 0.1)[1]
            for i in range(len(beta)):
                self.assertAlmostEqual(hess[i][j], (g_up[i] - g_down[i]) / (2 * h), places=4)

    def test_newton_converges_and_the_loss_never_rises(self):
        model = train_model.fit(simulated(1.0, 40), l2=0.1)
        opt = model["optimizer"]
        self.assertTrue(opt["converged"])
        self.assertLess(opt["final_grad_max"], train_model.TOL)
        self.assertLessEqual(opt["iterations"], 20)
        trace = opt["loss_trace"]
        self.assertTrue(all(b <= a + 1e-12 for a, b in zip(trace, trace[1:])))

    def test_converged_point_is_a_minimum(self):
        groups = simulated(1.0, 40)
        model = train_model.fit(groups, l2=0.1)
        data, weights = self._data(groups)
        best = train_model.objective(model["weights"], data, weights, 0.1)[0]
        for j in range(len(FEATURES)):
            for delta in (-0.05, 0.05):
                nudged = model["weights"][:]
                nudged[j] += delta
                self.assertGreater(train_model.objective(nudged, data, weights, 0.1)[0], best)

    def test_unfinished_fit_is_reported(self):
        model = train_model.fit(simulated(1.0, 10), max_iter=0)
        self.assertFalse(model["optimizer"]["converged"])

    def test_estimator_recovers_a_known_weight_and_calibrates(self):
        groups = simulated(1.2, 3000, seed=3)
        model = train_model.fit(groups, l2=1e-4)
        std = model["scaler"]["players_alive"]["std"]
        index = FEATURES.index("players_alive")
        self.assertAlmostEqual(model["weights"][index] / std, 1.2, delta=0.12)
        report = train_model.calibration(train_model.score_groups(model, groups))
        self.assertEqual(report["status"], "ok")
        self.assertLess(report["ece_game_weighted"], 0.03)


class MetricsTest(unittest.TestCase):
    def test_scores(self):
        uniform = train_model.score([0.5, 0.5], 0)
        self.assertAlmostEqual(uniform["log_loss"], math.log(2))
        self.assertAlmostEqual(uniform["brier"], 0.5)
        self.assertEqual(uniform["top1"], 0.5)
        self.assertEqual(
            train_model.players_baseline(group(1, 0, [{"players_alive": 2}, {"players_alive": 2}, {"players_alive": 1}])),
            [0.5, 0.5, 0.0],
        )

    def test_long_games_do_not_dominate_the_primary_metric(self):
        long_game = [group("long", 0, [{}, {}]) for _ in range(9)]
        short_game = [group("short", 0, [{}, {}, {}, {}])]
        model = {"weights": [0.0] * len(FEATURES), "scaler": train_model.fit_scaler(long_game + short_game)}
        summary = train_model.summarize(train_model.score_groups(model, long_game + short_game))
        self.assertAlmostEqual(summary["game_weighted"]["model"]["log_loss"], (math.log(2) + math.log(4)) / 2)
        self.assertAlmostEqual(summary["checkpoint_weighted"]["model"]["log_loss"], (9 * math.log(2) + math.log(4)) / 10)

    def test_calibration_needs_enough_games(self):
        model = train_model.fit(simulated(1.0, 10), l2=0.1)
        report = train_model.calibration(train_model.score_groups(model, simulated(1.0, 10)))
        self.assertEqual(report["status"], "insufficient_data")


class SplitTest(unittest.TestCase):
    def test_sessions_split_oldest_to_newest(self):
        groups = simulated(1.0, 50, session_of=lambda g: g // 10)
        split = train_model.session_split(groups, [0, 1, 2, 3, 4])
        self.assertEqual(split, {"train": [0, 1, 2], "validation": [3], "test": [4]})
        self.assertIsNone(train_model.session_split(groups, [0, 1]))
        thin = [g for g in groups if g["session_id"] != 4 or g["game_id"] < 42]
        self.assertIsNone(train_model.session_split(thin, [0, 1, 2, 3, 4]))

    def test_session_evaluation_never_fits_on_test(self):
        groups = simulated(1.0, 50, session_of=lambda g: g // 10)
        split = train_model.session_split(groups, [0, 1, 2, 3, 4])
        seen = []
        real = train_model.fit

        def spy(train, **kwargs):
            seen.append({g["session_id"] for g in train})
            return real(train, **kwargs)

        with mock.patch.object(train_model, "fit", side_effect=spy):
            result = train_model.evaluate_by_session(groups, split)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(4 not in sessions for sessions in seen))
        self.assertEqual(seen[: len(train_model.L2_GRID)], [{0, 1, 2}] * len(train_model.L2_GRID))
        self.assertEqual(result["test"]["overall"]["games"], 10)

    def test_game_held_out_is_labeled_weaker_and_excludes_the_held_game(self):
        groups = simulated(1.0, 6)
        seen = []
        real = train_model.fit

        def spy(train, **kwargs):
            seen.append({g["game_id"] for g in train})
            return real(train, **kwargs)

        with mock.patch.object(train_model, "fit", side_effect=spy):
            result = train_model.leave_one_game_out(groups)
        self.assertEqual(result["method"], "game_held_out_weaker")
        self.assertIn("weaker", result["warning"])
        for held, trained in zip(range(6), seen):
            self.assertNotIn(held, trained)

    def test_end_to_end_single_session_uses_the_weaker_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            out = Path(tmp) / "models"
            write_dataset(
                data,
                [{"game_id": g, "session_id": 1, "winner": 1 + g % 3, "payload": synthetic_game(winner=1 + g % 3)} for g in range(6)],
            )
            report = train_model.run(data, out, 5)
            self.assertEqual(report["status"], "trained")
            self.assertEqual(report["evaluation"]["method"], "game_held_out_weaker")
            self.assertEqual(report["dataset"]["checkpoint_groups"], 18)
            model = json.loads((out / "model.json").read_text())
            self.assertTrue(model["optimizer"]["converged"])
            self.assertEqual(model["features"], list(FEATURES))


if __name__ == "__main__":
    unittest.main()


class ContentionBaselineTest(unittest.TestCase):
    def _group(self, verified, eliminated):
        g = group(1, 0, [{}, {}, {}, {}])
        g["elimination_verified"] = verified
        for row, flag in zip(g["rows"], eliminated):
            row["eliminated"] = flag
        return g

    def test_eliminated_squads_get_zero_only_when_verified(self):
        flags = [False, True, False, True]
        self.assertEqual(train_model.contention_baseline(self._group(True, flags)), [0.5, 0.0, 0.5, 0.0])
        self.assertEqual(train_model.contention_baseline(self._group(False, flags)), [0.25] * 4)
        self.assertEqual(train_model.masked([0.4, 0.4, 0.1, 0.1], self._group(True, flags)), [0.8, 0.0, 0.2, 0.0])

    def test_candidate_summary_separates_no_standing_from_elimination(self):
        g = self._group(True, [False, True, False, False])
        g["rows"][1]["features"]["no_standing"] = 1.0
        g["rows"][2]["features"]["no_standing"] = 1.0
        g["teams_alive"] = 3
        summary = train_model.candidate_summary([g])
        self.assertEqual(summary["in_contention"], {"3": 1})
        self.assertEqual(summary["in_contention_equals_teams_alive"], 1)
        self.assertEqual(summary["status_vs_elimination_rows"]["no_standing_eliminated"], 1)
        self.assertEqual(summary["status_vs_elimination_rows"]["no_standing_not_eliminated"], 1)


class FeatureSubsetTest(unittest.TestCase):
    def test_condition_only_fit_uses_only_condition_features(self):
        model = train_model.fit(simulated(1.0, 40), features=train_model.CONDITION_FEATURES)
        self.assertEqual(model["features"], list(train_model.CONDITION_FEATURES))
        self.assertEqual(list(model["scaler"]), list(train_model.CONDITION_FEATURES))
        self.assertEqual(len(train_model.predict(model, simulated(1.0, 1)[0])), 4)
        self.assertTrue(set(train_model.POSITION_FEATURES).isdisjoint(train_model.CONDITION_FEATURES))
        self.assertEqual(set(train_model.POSITION_FEATURES) | set(train_model.CONDITION_FEATURES), set(FEATURES))
