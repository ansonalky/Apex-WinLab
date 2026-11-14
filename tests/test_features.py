"""Feature rows from decoder snapshots. Synthetic series, no network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from features import FEATURES, build_dataset, game_examples, load_accepted, team_rows


def player(team: int, x: float, y: float, status: str = "alive", hp: int | None = 100) -> dict:
    body = {
        "name": f"p{team}",
        "teamId": team,
        "teamName": f"T{team}",
        "pos": {"x": x, "y": y, "z": 0},
        "status": status,
        "maxHealth": 100,
        "shieldHealth": 50,
        "shieldMaxHealth": 50,
    }
    if hp is not None:
        body["currentHealth"] = hp
    return body


def synthetic_game(winner: int = 1, teams: int = 3, losers_out_at: int = 6) -> dict:
    """Two players per team. Every team but ``winner`` is eliminated at ``losers_out_at``.

    Playing runs ``t`` 2 through 8, so the default checkpoints are 2, 5, 8.
    """
    players = {}
    for team in range(1, teams + 1):
        for slot in range(2):
            players[f"t{team}s{slot}"] = player(team, team * 100.0 + slot, team * 10.0)
    series = [
        {"t": 0, "d": {"state": ["Prematch"], "teamsAlive": [teams], "players": [players]}},
        {"t": 2, "d": {"state": ["Prematch", "Playing"]}},
    ]
    eliminate = {}
    for team in range(1, teams + 1):
        if team == winner:
            continue
        for slot in range(2):
            eliminate[f"t{team}s{slot}"] = {"status": ["alive", "eliminated"]}
    series.append({"t": losers_out_at, "d": {"players": eliminate, "teamsAlive": [teams, 1]}})
    series.append({"t": 9, "d": {"state": ["Playing", "WinnerDetermined"]}})
    series.append({"t": 10, "d": {"state": ["WinnerDetermined", "Postmatch"]}})
    return {"series": series}


def snapshot_row(team, x, y, status="alive", hp=100, gap=0):
    leaf = lambda value, g=gap: {"value": value, "observed_at": 0, "gap": g}
    return {
        "nucleusHash": f"{team}-{x}",
        "name": leaf("n"),
        "teamId": leaf(team),
        "teamName": leaf(f"T{team}"),
        "pos": {
            "value": {"x": x, "y": y, "z": 0},
            "observed_at": {"x": 0, "y": 0, "z": 0},
            "gap": {"x": gap, "y": gap, "z": gap},
        },
        "health": {"current": leaf(hp), "max": leaf(100)},
        "shields": {"current": leaf(50), "max": leaf(50)},
        "status": leaf(status),
    }


class TeamRowsTest(unittest.TestCase):
    def test_teams_fully_out_are_not_candidates(self):
        snap = [
            snapshot_row(1, 0, 0),
            snapshot_row(1, 1, 0, status="downed"),
            snapshot_row(2, 10, 0, status="dead"),
            snapshot_row(2, 11, 0, status="eliminated"),
        ]
        rows = team_rows(snap, 5, (0, 10))
        self.assertEqual([row["team"] for row in rows], [1])
        features = rows[0]["features"]
        self.assertEqual(features["players_alive"], 1)
        self.assertEqual(features["players_downed"], 1)
        self.assertEqual(features["teams_standing"], 1)
        self.assertAlmostEqual(features["progress"], 0.5)
        self.assertEqual(set(features), set(FEATURES))

    def test_unknown_health_stays_unknown(self):
        snap = [snapshot_row(1, 0, 0, hp=None), snapshot_row(2, 5, 0)]
        rows = {row["team"]: row["features"] for row in team_rows(snap, 0, (0, 10))}
        self.assertIsNone(rows[1]["hp_frac"])
        self.assertEqual(rows[1]["hp_frac_missing"], 1.0)
        self.assertAlmostEqual(rows[2]["hp_frac"], 1.0)
        self.assertEqual(rows[2]["hp_frac_missing"], 0.0)

    def test_distances_are_relative_within_the_snapshot(self):
        snap = [snapshot_row(1, 0, 0), snapshot_row(2, 10, 0), snapshot_row(3, 1000, 0)]
        scaled = [snapshot_row(1, 0, 0), snapshot_row(2, 100, 0), snapshot_row(3, 10000, 0)]
        a = {row["team"]: row["features"]["center_dist_rel"] for row in team_rows(snap, 0, (0, 1))}
        b = {row["team"]: row["features"]["center_dist_rel"] for row in team_rows(scaled, 0, (0, 1))}
        for team in a:
            self.assertAlmostEqual(a[team], b[team])
        self.assertGreater(a[3], a[1])

    def test_carried_position_counts_as_stale(self):
        snap = [snapshot_row(1, 0, 0, gap=0), snapshot_row(1, 1, 0, gap=31), snapshot_row(2, 9, 0)]
        rows = {row["team"]: row["features"] for row in team_rows(snap, 0, (0, 1))}
        self.assertAlmostEqual(rows[1]["stale_pos_frac"], 0.5)
        self.assertEqual(rows[2]["stale_pos_frac"], 0.0)


class GameExamplesTest(unittest.TestCase):
    def test_label_is_the_final_winner_and_checkpoints_are_playing(self):
        game = game_examples(synthetic_game(winner=2), "g1")
        self.assertEqual(game["winner"], 2)
        self.assertEqual([group["t"] for group in game["groups"]], [2, 5, 8])
        for group in game["groups"]:
            self.assertEqual(group["rows"][group["label"]]["team"], 2)
        self.assertEqual(len(game["groups"][1]["rows"]), 3)
        self.assertEqual(len(game["groups"][2]["rows"]), 1)

    def test_rows_do_not_see_writes_after_their_checkpoint(self):
        early = game_examples(synthetic_game(losers_out_at=4), "g")
        late = game_examples(synthetic_game(losers_out_at=7), "g")
        self.assertEqual(early["groups"][0]["rows"], late["groups"][0]["rows"])
        self.assertNotEqual(early["groups"][1]["rows"], late["groups"][1]["rows"])
        self.assertEqual(len(late["groups"][1]["rows"]), 3)

    def test_unknown_outcome_produces_no_rows(self):
        payload = synthetic_game()
        payload["series"][2]["d"]["teamsAlive"] = [3, 2]
        game = game_examples(payload, "g")
        self.assertEqual(game["groups"], [])
        self.assertEqual(game["skipped"], [{"reason": "outcome_unknown"}])


class LoadAcceptedTest(unittest.TestCase):
    def test_only_accepted_records_with_raw_files_and_unique_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "raw").mkdir()
            (data / "collection").mkdir()
            for live in ("a", "b"):
                (data / "raw" / f"{live}.timeseries.json").write_text(json.dumps(synthetic_game()))
            games = {
                "1": {"game_id": 1, "session_id": 9, "livedata_id": "a", "sha256": "h1", "acceptance_status": "accepted"},
                "2": {"game_id": 2, "session_id": 9, "livedata_id": "b", "sha256": "h1", "acceptance_status": "accepted"},
                "3": {"game_id": 3, "session_id": 9, "livedata_id": "c", "acceptance_status": "accepted"},
                "4": {"game_id": 4, "session_id": 9, "livedata_id": "b", "acceptance_status": "excluded"},
            }
            (data / "collection" / "manifest.json").write_text(json.dumps({"games": games}))
            records, inventory = load_accepted(data)
            self.assertEqual([record["game_id"] for record in records], [1])
            self.assertEqual(inventory["accepted_in_manifest"], 3)
            self.assertEqual(inventory["accepted_missing_raw"], [3])
            self.assertEqual(inventory["duplicate_payloads"], [{"game_id": 2, "same_as": 1}])
            groups, dropped = build_dataset(records)
            self.assertEqual(len(groups), 3)
            self.assertEqual(dropped, [])

    def test_missing_manifest_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            records, inventory = load_accepted(Path(tmp))
        self.assertEqual(records, [])
        self.assertFalse(inventory["manifest_exists"])

    def test_manifest_winner_disagreement_drops_the_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "g.json"
            raw.write_text(json.dumps(synthetic_game(winner=1)))
            record = {"game_id": 7, "raw_path": str(raw), "validation": {"winner": {"teamId": 3}}}
            groups, dropped = build_dataset([record])
        self.assertEqual(groups, [])
        self.assertEqual(dropped[0]["reason"], "winner_mismatch_with_manifest")


if __name__ == "__main__":
    unittest.main()
