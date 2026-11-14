"""Tiger row contract: replay-clock timestamps, provenance, and safe re-import.

No database here. ``import_game`` runs against a recording fake connection;
the SQL itself is exercised against a real Postgres outside this suite.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path

import import_tiger
from tiger_rows import FITTED, HELD_OUT, frame_at, player_tuples, replay_timestamp

DATA = Path(__file__).resolve().parents[1] / "data"


def _timeline(start="1700000010"):
    player = {
        "id": "a", "name": "A", "teamId": 1, "teamName": "Red", "x": None, "y": 2,
        "health": None, "healthMax": 100, "shields": None, "shieldMax": None, "status": None,
    }
    return {
        "replay_match_start": start,
        "playing_start": 10,
        "frames": [
            {"t": 10, "elapsed": 0, "players": [player]},
            {"t": 11, "elapsed": 1, "players": [dict(player, x=3, health=9, status="alive")]},
        ],
    }


class RowContractTest(unittest.TestCase):
    def test_timestamp_is_replay_match_start_plus_t_not_manifest(self):
        record = {"game_id": 7, "match_start": 1_700_000_000}
        rows = player_tuples(record, _timeline("1700000010"))
        self.assertEqual(rows[0][0].timestamp(), 1_700_000_010 + 10)
        self.assertEqual(replay_timestamp("1700000010", 100).timestamp(), 1_700_000_110)

    def test_missing_replay_clock_is_refused(self):
        with self.assertRaises(SystemExit):
            player_tuples({"game_id": 7, "match_start": 1_700_000_000}, _timeline(None))

    def test_missing_values_stay_none(self):
        rows = player_tuples({"game_id": 7, "match_start": 0}, _timeline())
        self.assertIsNone(rows[0][7])
        self.assertIsNone(rows[0][9])
        self.assertIsNone(rows[0][13])
        self.assertEqual(frame_at(rows, 10)[0][2], 10)
        self.assertEqual(frame_at(rows, 100)[0][2], 11)


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split())[:60], params))

    def executemany(self, sql, rows):
        self.log.append((" ".join(sql.split())[:60], len(list(rows))))

    @contextmanager
    def copy(self, sql):
        class Copy:
            def write_row(_self, row):
                pass

        self.log.append(("COPY", None))
        yield Copy()


class FakeConnection:
    def __init__(self):
        self.log = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return FakeCursor(self.log)


class FakeSource:
    game_id = 7
    match = (7,)
    squads = [(7, 1, "Red")]
    players = []

    def __init__(self, held, fitted):
        self.estimates = {HELD_OUT: (held, "x"), FITTED: (fitted, "y")}


class ReimportTest(unittest.TestCase):
    def _deletes(self, log):
        return [params for sql, params in log if sql.startswith("DELETE FROM squad_estimates")]

    def test_missing_kind_is_kept_not_deleted(self):
        connection = FakeConnection()
        result = import_tiger.import_game(connection, FakeSource([], [("row",)]), report=None)
        self.assertEqual(result["replaced_kinds"], [FITTED])
        self.assertEqual(result["kept_kinds"], [HELD_OUT])
        self.assertEqual(self._deletes(connection.log), [(7, FITTED)])

    def test_frames_only_touches_no_estimates_or_reports(self):
        connection = FakeConnection()
        import_tiger.import_game(connection, FakeSource([("h",)], [("f",)]), report=("r",), frames_only=True)
        self.assertEqual(self._deletes(connection.log), [])
        self.assertFalse(any(sql.startswith("INSERT INTO evaluation_reports") for sql, _ in connection.log))
        self.assertFalse(any(sql.startswith("DELETE FROM evaluation_reports") for sql, _ in connection.log))

    def test_redact_hides_connection_details(self):
        dsn = "postgres://user:secret@example.com:5432/db"
        text = f'connection to server at "1.2.3.4", port 5432 failed for {dsn}'
        cleaned = import_tiger.redact(text, dsn)
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("1.2.3.4", cleaned)


@unittest.skipUnless((DATA / "collection" / "manifest.json").is_file(), "collection data is not on this machine")
class RealDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dashboard_data import MatchStore

        cls.store = MatchStore(DATA)

    def test_train_game_gets_no_held_out_rows(self):
        source = import_tiger.Source(DATA, 123349, self.store)
        rows, status = source.estimates[HELD_OUT]
        self.assertEqual(rows, [])
        if self.store.bundle.get("available"):
            self.assertIn("train", status)
        self.assertTrue(all(row[5] == FITTED for row in source.estimates[FITTED][0]))
        offset = source.players[0][0].timestamp() - source.players[0][2]
        self.assertEqual(offset, int(source.timeline["replay_match_start"]))
        self.assertNotEqual(offset, source.record["match_start"])

    @unittest.skipUnless((DATA / "exports" / "heldout-session-split-v2" / "bundle.json").is_file(), "v2 bundle not exported")
    def test_test_game_held_out_rows_come_from_the_bundle(self):
        source = import_tiger.Source(DATA, 123633, self.store)
        rows, _status = source.estimates[HELD_OUT]
        bundle = json.loads((DATA / "exports" / "heldout-session-split-v2" / "heldout_predictions.json").read_text())
        expected = {
            (entry["t"], squad["team_id"]): squad["probabilities"]
            for entry in bundle
            if entry["game_id"] == 123633
            for squad in entry["squads"]
        }
        self.assertEqual(len(rows), len(expected))
        for row in rows:
            probabilities = expected[(row[1], row[4])]
            self.assertEqual(row[5], HELD_OUT)
            self.assertIn("heldout-session-split-v2", row[6])
            self.assertEqual(row[7], probabilities["model_constrained"])
            self.assertEqual(row[8], probabilities["model"])
            if row[12] and row[11]:
                self.assertEqual(row[7], 0.0)


if __name__ == "__main__":
    unittest.main()
