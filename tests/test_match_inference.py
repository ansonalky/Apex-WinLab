"""Forward pass of the saved model. No refit and no frozen-split edits."""

from __future__ import annotations

import json
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from psycopg.pq import TransactionStatus

import importlib.util

from features import FEATURES
from match_inference import (
    LABEL,
    NEW_MATCH,
    UNVERIFIED,
    build_table,
    classify,
    describe,
    incompatible_reason,
    persist_new_match,
    training_inventory,
)
def _load_fixture():
    path = Path(__file__).with_name("replay_fixture.py")
    spec = importlib.util.spec_from_file_location("replay_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


series = _load_fixture().series

DATA = Path(__file__).resolve().parent.parent / "data"
MODEL = DATA / "models" / "model.json"


def _dummy_model() -> dict:
    return {
        "features": list(FEATURES),
        "weights": [0.0] * len(FEATURES),
        "scaler": {name: {"mean": 0.0, "std": 1.0} for name in FEATURES},
    }


def _inventory(ids, hashes):
    return {
        "game_ids": set(ids),
        "hashes": dict(hashes),
        "by_hash": {digest: game_id for game_id, digest in hashes.items()},
        "complete": bool(ids) and len(hashes) == len(set(ids)),
    }


class ClassifyTest(unittest.TestCase):
    def test_original_verified_unseen_and_the_unverified_cases(self):
        inventory = _inventory([10, 11], {10: "a" * 64, 11: "b" * 64})
        self.assertEqual(classify(10, "a" * 64, inventory)["membership"], "original")
        unseen = classify(99, "c" * 64, inventory)
        self.assertEqual(unseen["membership"], "verified_unseen")
        self.assertEqual(unseen["reason"], LABEL)
        self.assertEqual(classify(99, "a" * 64, inventory)["membership"], "unverified")
        self.assertIn("matches training game 10", classify(99, "a" * 64, inventory)["reason"])
        self.assertEqual(classify(10, "d" * 64, inventory)["membership"], "unverified")
        self.assertEqual(classify(99, None, inventory)["membership"], "unverified")
        incomplete = _inventory([10], {})
        self.assertEqual(classify(99, "c" * 64, incomplete)["membership"], "unverified")
        self.assertIn(UNVERIFIED, classify(99, None, inventory)["reason"])


class BuildTableTest(unittest.TestCase):
    def test_probabilities_mask_eliminations_and_stop_at_the_cutoff(self):
        payload = series(eliminate=2)
        built = build_table(payload["series"], _dummy_model())
        self.assertTrue(built["ok"], built["reason"])
        self.assertNotIn(built["cutoff_t"], built["table"])
        self.assertTrue(built["table"])
        self.assertTrue(all(t < built["cutoff_t"] for t in built["table"]))
        masked = built["table"][8]
        by_team = {squad["teamId"]: squad for squad in masked["squads"]}
        self.assertEqual(by_team[2]["model"], 0.0)
        self.assertTrue(by_team[2]["eliminated"])
        total = sum(squad["model"] for squad in masked["squads"])
        self.assertAlmostEqual(total, 1.0, places=6)
        earlier = built["table"][4]
        self.assertTrue(all(not squad["eliminated"] for squad in earlier["squads"]))
        self.assertAlmostEqual(sum(squad["model"] for squad in earlier["squads"]), 1.0, places=6)

    @unittest.skipUnless(MODEL.is_file(), "saved model.json is not in data/")
    def test_saved_model_scores_a_short_replay_without_refitting(self):
        before = MODEL.read_bytes()
        built = build_table(series()["series"], json.loads(before))
        self.assertEqual(MODEL.read_bytes(), before)
        self.assertTrue(built["ok"], built["reason"])
        point = next(iter(built["table"].values()))
        self.assertAlmostEqual(sum(squad["model"] for squad in point["squads"]), 1.0, places=6)
        self.assertTrue(all(squad["model"] >= 0 for squad in point["squads"]))

    @unittest.skipUnless(MODEL.is_file(), "saved model.json is not in data/")
    def test_an_expansion_id_is_verified_unseen_without_replaying_the_set(self):
        inventory = training_inventory(DATA)
        self.assertTrue(inventory["complete"], "every trained game needs a manifest hash")
        self.assertGreaterEqual(len(inventory["game_ids"]), 51)
        classified = classify(880001, "e" * 64, inventory)
        self.assertEqual(classified["membership"], "verified_unseen")
        self.assertEqual(classified["reason"], LABEL)
        self.assertIsNone(incompatible_reason(json.loads(MODEL.read_text(encoding="utf-8"))))


class PersistTest(unittest.TestCase):
    def test_reimport_replaces_only_that_games_new_match_rows(self):
        connection = MemoryConnection()
        held = _row(7, "held_out")
        other = _row(8, NEW_MATCH)
        connection.estimates = [held, other]
        table = build_table(series(eliminate=2)["series"], _dummy_model())["table"]
        provenance = {"kind": NEW_MATCH, "membership": "verified_unseen", "sha256": "m"}
        first = persist_new_match(connection, 7, table, provenance=provenance, replay_match_start=1000)
        second = persist_new_match(connection, 7, table, provenance=provenance, replay_match_start=1000)
        kinds = [(row[0], row[5]) for row in connection.estimates]
        self.assertIn((7, "held_out"), kinds)
        self.assertIn((8, NEW_MATCH), kinds)
        self.assertEqual(sum(1 for row in kinds if row == (7, NEW_MATCH)), first["rows"])
        self.assertEqual(first["rows"], second["rows"])
        self.assertGreater(first["rows"], 0)
        with self.assertRaises(RuntimeError):
            persist_new_match(connection, 7, table, provenance={"kind": NEW_MATCH, "membership": "original"}, replay_match_start=1000)


class DescribeTest(unittest.TestCase):
    @unittest.skipUnless(MODEL.is_file(), "saved model.json is not in data/")
    def test_saved_import_and_preview_share_the_forward_pass(self):
        inventory = training_inventory(DATA)
        if not inventory["complete"]:
            self.skipTest("training inventory incomplete")
        import tempfile

        from dashboard_data import clear_expansion_dirs, expansion_dirs, register_expansion_dir
        from live_predictions import NEW_MATCH, LivePredictions
        from preview_store import configure, preview_record, write_preview

        previous_dirs = list(expansion_dirs())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models").mkdir()
            shutil.copy(MODEL, root / "models" / "model.json")
            games = {
                str(game_id): {"game_id": game_id, "sha256": digest, "acceptance_status": "accepted"}
                for game_id, digest in inventory["hashes"].items()
            }
            manifest = root / "collection" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"games": games}), encoding="utf-8")
            imports = root / "user-imports"
            raw = json.dumps(series()).encode()
            record = {
                "game_id": 880001,
                "session_id": 1,
                "livedata_id": "ld-880001",
                "quality_score": 7.0,
                "acceptance_status": "accepted",
                "sha256": "pending",
                "map_name": "World's Edge",
                "event": "Fixture",
            }
            from download_match import sha256_bytes

            record["sha256"] = sha256_bytes(raw)
            (imports / "raw").mkdir(parents=True)
            (imports / "raw" / "ld-880001.timeseries.json").write_bytes(raw)
            (imports / "collection").mkdir(parents=True)
            (imports / "collection" / "manifest.json").write_text(
                json.dumps({"package": "user-import", "games": {"880001": {**record, "download_path": "raw/ld-880001.timeseries.json"}}}),
                encoding="utf-8",
            )
            register_expansion_dir(imports)
            try:
                saved = describe(root, 880001)
                table = LivePredictions(root).table(880001, NEW_MATCH)
            finally:
                clear_expansion_dirs()
                for path in previous_dirs:
                    register_expansion_dir(path)
            self.assertEqual(saved["role"], "new_match")
            self.assertEqual(saved["membership"], "verified_unseen")
            self.assertEqual(saved["storage"], "saved")
            self.assertTrue(saved["predict"])
            self.assertEqual(saved["label"], LABEL)
            self.assertTrue(table)
            self.assertAlmostEqual(sum(squad["model"] for squad in next(iter(table.values()))["squads"]), 1.0, places=6)
            configure(root / "previews")
            preview = dict(record)
            preview["game_id"] = 880002
            preview["quality_score"] = 6.9
            preview["acceptance_status"] = "preview"
            preview["livedata_id"] = "ld-880002"
            write_preview(880002, preview, raw)
            found = preview_record(880002)
            self.assertIsNotNone(found)
            described = describe(root, 880002)
            self.assertEqual(described["storage"], "preview")
            self.assertEqual(described["membership"], "verified_unseen")
            self.assertIn("has not been established", described["limitation"])
            self.assertTrue(described["predict"])
            preview_table = LivePredictions(root).table(880002, NEW_MATCH)
            self.assertTrue(preview_table)
            self.assertAlmostEqual(
                sum(squad["model"] for squad in next(iter(preview_table.values()))["squads"]), 1.0, places=6
            )


def _row(game_id: int, kind: str) -> tuple:
    return (game_id, 0, 0, None, 1, kind, "{}", 0.5, 0.5, "uniform_contention", 0.5, True, False, True)


class MemoryCursor:
    def __init__(self, connection: "MemoryConnection"):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.connection.statements.append(text)
        if text.startswith("SELECT pg_get_constraintdef"):
            self.connection._fetch = ("CHECK (prediction_kind IN ('held_out', 'final_model_fitted'))",) if self.connection.constraint_old else ("CHECK (prediction_kind IN ('held_out', 'final_model_fitted', 'new_match'))",)
            return
        if text.startswith("ALTER TABLE"):
            if "ADD CONSTRAINT" in text:
                self.connection.constraint_old = False
            return
        if text.startswith("DELETE FROM squad_estimates"):
            game_id, kind = params
            self.connection.estimates = [row for row in self.connection.estimates if not (row[0] == game_id and row[5] == kind)]
            return
        raise AssertionError(text)

    def executemany(self, sql, rows):
        text = " ".join(sql.split())
        self.connection.statements.append(text)
        if text.startswith("INSERT INTO squad_estimates"):
            self.connection.estimates.extend(rows)
            return
        raise AssertionError(text)

    def fetchone(self):
        return self.connection._fetch


class MemoryConnection:
    def __init__(self):
        self.estimates: list[tuple] = []
        self.statements: list[str] = []
        self.constraint_old = True
        self._fetch = None
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def cursor(self):
        return MemoryCursor(self)

    @contextmanager
    def transaction(self):
        self.info.transaction_status = TransactionStatus.INTRANS
        snapshot = list(self.estimates)
        try:
            yield
        except Exception:
            self.estimates = snapshot
            raise
        finally:
            self.info.transaction_status = TransactionStatus.IDLE


if __name__ == "__main__":
    unittest.main()
