"""Training refuses thin data and evaluates only on held-out games."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import train_model
from features import FEATURES
from tests.test_features import synthetic_game


def write_games(data: Path, count: int) -> None:
    (data / "raw").mkdir(parents=True)
    (data / "collection").mkdir(parents=True)
    games = {}
    for index in range(count):
        live = f"live{index}"
        payload = synthetic_game(winner=1 + index % 3)
        (data / "raw" / f"{live}.timeseries.json").write_text(json.dumps(payload))
        games[str(index)] = {
            "game_id": index,
            "session_id": 1,
            "livedata_id": live,
            "sha256": f"h{index}",
            "acceptance_status": "accepted",
        }
    (data / "collection" / "manifest.json").write_text(json.dumps({"games": games}))


def group(game_id, label, alive):
    rows = []
    for count in alive:
        features = {name: 0.0 for name in FEATURES}
        features["players_alive"] = float(count)
        rows.append({"team": len(rows), "features": features})
    return {"game_id": game_id, "t": 0, "checkpoint": 0, "rows": rows, "label": label}


class InsufficientDataTest(unittest.TestCase):
    def test_no_manifest_reports_and_writes_no_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "models"
            report = train_model.run(Path(tmp) / "data", out, 20, 3)
            self.assertEqual(report["status"], "insufficient_data")
            self.assertIsNone(report["evaluation"])
            self.assertFalse((out / "model.json").exists())

    def test_below_min_games_does_not_train(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            write_games(data, 4)
            with mock.patch.object(train_model, "fit") as fit:
                report = train_model.run(data, Path(tmp) / "models", 5, 3)
            fit.assert_not_called()
            self.assertEqual(report["status"], "insufficient_data")
            self.assertEqual(report["inventory"]["usable"], 4)

    def test_min_games_has_a_floor(self):
        with self.assertRaises(SystemExit):
            train_model.run(Path("nowhere"), Path("nowhere"), train_model.MIN_GAMES_FLOOR - 1, 3)

    def test_cli_exit_code_is_nonzero_without_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = train_model.main(["--data-dir", f"{tmp}/data", "--out-dir", f"{tmp}/models"])
            report = json.loads((Path(tmp) / "models" / "report.json").read_text())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "insufficient_data")


class EvaluationTest(unittest.TestCase):
    def test_each_fold_trains_without_its_held_out_game(self):
        groups = [group(g, g % 2, [2, 1]) for g in range(6)]
        seen = []
        real_fit = train_model.fit

        def spy(train, **kwargs):
            seen.append({item["game_id"] for item in train})
            return real_fit(train, **kwargs)

        with mock.patch.object(train_model, "fit", side_effect=spy):
            result = train_model.leave_one_game_out(groups, steps=5)
        self.assertEqual(result["folds"], 6)
        self.assertEqual(len(seen), 6)
        for held, trained in zip(range(6), seen):
            self.assertNotIn(held, trained)
            self.assertEqual(len(trained), 5)

    def test_model_learns_a_clear_signal(self):
        groups = [group(g, g % 3, [3 if i == g % 3 else 1 for i in range(3)]) for g in range(9)]
        result = train_model.leave_one_game_out(groups)
        overall = result["overall"]
        self.assertEqual(overall["model"]["top1"], 1.0)
        self.assertLess(overall["model"]["log_loss"], overall["uniform"]["log_loss"])

    def test_ties_split_top1_credit(self):
        self.assertEqual(train_model.score([0.5, 0.5], 0)["top1"], 0.5)
        self.assertEqual(train_model.players_baseline(group(1, 0, [2, 2, 1])), [0.5, 0.5, 0.0])

    def test_end_to_end_on_synthetic_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            out = Path(tmp) / "models"
            write_games(data, 6)
            report = train_model.run(data, out, 5, 3, steps=20)
            self.assertEqual(report["status"], "trained")
            self.assertEqual(report["evaluation"]["folds"], 6)
            self.assertEqual(set(report["evaluation"]["by_checkpoint"]), {"0", "1", "2"})
            model = json.loads((out / "model.json").read_text())
            self.assertEqual(model["features"], list(FEATURES))
            self.assertEqual(sorted(model["trained_on_games"]), list(range(6)))


if __name__ == "__main__":
    unittest.main()
