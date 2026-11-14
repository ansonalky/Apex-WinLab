"""Freeze and export refuse anything that does not reproduce the frozen evaluation."""

from __future__ import annotations

import copy
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import export_heldout
import train_model
from features import load_accepted
from tests.test_features import synthetic_game, write_dataset


def multi_session(data: Path) -> None:
    games = []
    for index in range(25):
        winner = 1 + index % 3
        payload = synthetic_game(winner=winner, out_at=40 + (index % 4) * 10, decided_at=100 + index)
        payload["series"][0]["d"]["match_start"] = [str(2_000_000 + index * 100)]
        games.append(
            {
                "game_id": 1000 + index,
                "session_id": 50 + index // 5,
                "winner": winner,
                "match_start": 1_000_000 + index * 100,
                "payload": payload,
            }
        )
    write_dataset(data, games)
    # The shared fixture uses placeholder hashes; export checks real ones.
    path = data / "collection" / "manifest.json"
    manifest = json.loads(path.read_text())
    for record in manifest["games"].values():
        raw = data / "raw" / f"{record['livedata_id']}.timeseries.json"
        record["sha256"] = export_heldout._sha256(raw.read_bytes())
    path.write_text(json.dumps(manifest))


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        multi_session(self.data)
        self.report = train_model.run(self.data, Path(self.tmp.name) / "models", 20)
        self.records, _ = load_accepted(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_uses_a_session_split(self):
        self.assertEqual(self.report["status"], "trained")
        self.assertEqual(self.report["evaluation"]["method"], "session_split")

    def test_export_reproduces_frozen_metrics_and_writes_a_bundle(self):
        config = export_heldout.freeze(self.report, self.records)
        export = export_heldout.build_export(config, self.data)
        out = Path(self.tmp.name) / "exports"
        written = export_heldout.write_bundle(export, config, out, "b1", self.data)
        bundle = json.loads((out / "b1" / "bundle.json").read_text())
        self.assertTrue(bundle["held_out"])
        self.assertFalse(bundle["final_model_predictions_included"])
        self.assertEqual(bundle["prediction_type"], export_heldout.PREDICTION_TYPE)
        predictions = json.loads((out / "b1" / "heldout_predictions.json").read_text())
        self.assertEqual({p["game_id"] for p in predictions}, set(config["games"]["test"]))
        for point in predictions:
            self.assertEqual(sum(1 for s in point["squads"] if s["is_winner"]), 1)
            total = sum(s["probabilities"]["model"] for s in point["squads"])
            self.assertAlmostEqual(total, 1.0)
            # Replay clock, not the manifest's games-API match_start.
            self.assertEqual(point["replay_time_unix"], 2_000_000 + (point["game_id"] - 1000) * 100 + point["t"])
        for point in predictions:
            constrained = [s["probabilities"]["model_constrained"] for s in point["squads"]]
            self.assertAlmostEqual(sum(constrained), 1.0)
            for squad in point["squads"]:
                if point["eligibility"] == "verified" and squad["eliminated"]:
                    self.assertEqual(squad["probabilities"]["model_constrained"], 0.0)
                elif point["eligibility"] != "verified":
                    self.assertEqual(squad["probabilities"]["model_constrained"], squad["probabilities"]["model"])
        metrics = json.loads((out / "b1" / "metrics.json").read_text())
        self.assertEqual(
            metrics["constrained"]["game_weighted"],
            config["test_metrics"]["game_weighted"]["model_masked_diagnostic"],
        )
        self.assertEqual(bundle["schema_version"], 2)
        self.assertEqual(bundle["primary_model_probability"], "model_constrained")
        model = json.loads((out / "b1" / "evaluation_model.json").read_text())
        self.assertTrue(set(model["trained_on_games"]).isdisjoint(config["games"]["test"]))
        with tarfile.open(written["archive"]) as tar:
            self.assertEqual(
                sorted(tar.getnames()),
                sorted(f"b1/{name}" for name in ("README.md", "bundle.json", "evaluation_model.json", "heldout_predictions.json", "metrics.json")),
            )
        with self.assertRaises(SystemExit):
            export_heldout.write_bundle(export, config, out, "b1", self.data)

    def test_changed_frozen_metric_is_refused(self):
        config = export_heldout.freeze(self.report, self.records)
        tampered = copy.deepcopy(config)
        tampered["test_metrics"]["game_weighted"]["model"]["log_loss"] += 0.01
        with self.assertRaises(SystemExit):
            export_heldout.build_export(tampered, self.data)

    def test_changed_data_is_refused(self):
        config = export_heldout.freeze(self.report, self.records)
        tampered = copy.deepcopy(config)
        tampered["data_sha256"][str(self.records[0]["game_id"])] = "0" * 64
        with self.assertRaises(SystemExit):
            export_heldout.build_export(tampered, self.data)

    def test_freeze_needs_a_session_split(self):
        report = copy.deepcopy(self.report)
        report["evaluation"]["method"] = "game_held_out_weaker"
        with self.assertRaises(SystemExit):
            export_heldout.freeze(report, self.records)


if __name__ == "__main__":
    unittest.main()
