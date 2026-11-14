"""Feature groups from decoder snapshots. Synthetic series, no network.

These tests check extraction rules. They are not evidence of model quality.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import features
from features import (
    FEATURES,
    STANDINGS_SOURCE,
    build_dataset,
    game_examples,
    load_accepted,
    replay_checkpoints,
    squad_rows,
)
from overstat_patch import replay

PLAYING_AT = 10


def synthetic_game(winner=1, teams=3, out_at=50, decided_at=100, winner_out=()):
    """Two players per squad. Playing starts at t 10.

    Losers are ``eliminated`` at ``out_at``. ``teamsAlive`` drops to 1 and
    placements are written at ``decided_at``; the state leaves Playing five
    seconds later. ``winner_out`` lists ``(t, status)`` writes applied to
    both winner players, to model a winning squad that shows no standing
    player for a while.
    """
    players = {}
    for team in range(1, teams + 1):
        for slot in range(2):
            players[f"t{team}s{slot}"] = {
                "name": f"p{team}{slot}",
                "teamId": team,
                "teamName": f"T{team}",
                "pos": {"x": team * 100.0 + slot, "y": team * 10.0, "z": 0},
                "status": "alive",
                "currentHealth": 100,
                "maxHealth": 100,
                "shieldHealth": 50,
                "shieldMaxHealth": 50,
            }
    entries = {
        0: {"state": ["Prematch"], "teamsAlive": [teams], "players": [players]},
        PLAYING_AT: {"state": ["Prematch", "Playing"]},
    }
    losers = {}
    for team in range(1, teams + 1):
        if team != winner:
            for slot in range(2):
                losers[f"t{team}s{slot}"] = {"status": ["alive", "eliminated"]}
    entries.setdefault(out_at, {}).setdefault("players", {}).update(losers)
    previous = "alive"
    for t, status in winner_out:
        writes = {f"t{winner}s{slot}": {"status": [previous, status]} for slot in range(2)}
        entries.setdefault(t, {}).setdefault("players", {}).update(writes)
        previous = status
    placement = {
        f"t{team}s{slot}": {"teamPlacement": [1 if team == winner else 2]}
        for team in range(1, teams + 1)
        for slot in range(2)
    }
    decided = entries.setdefault(decided_at, {})
    decided["teamsAlive"] = [teams, 1]
    for key, value in placement.items():
        decided.setdefault("players", {}).setdefault(key, {}).update(value)
    entries[decided_at + 5] = {"state": ["Playing", "WinnerDetermined"]}
    entries[decided_at + 6] = {"state": ["WinnerDetermined", "Postmatch"]}
    return {"series": [{"t": t, "d": d} for t, d in sorted(entries.items())]}


def standings(game_id, winner=1, teams=3, name=None):
    return {
        "id": game_id,
        "teams": [
            {
                "teamId": team,
                "name": name if (team == winner and name) else f"T{team}",
                "overall_stats": {"teamPlacement": 1 if team == winner else 2},
            }
            for team in range(1, teams + 1)
        ],
    }


def record(game_id, winner=1, session_id=1, source=None, **extra):
    return {
        "game_id": game_id,
        "session_id": session_id,
        "acceptance_status": "accepted",
        "validation": {
            "winner": {
                "teamId": winner,
                "teamName": f"T{winner}",
                "label_source": source or f"replay.teamsAlive + {STANDINGS_SOURCE}",
            }
        },
        **extra,
    }


def snapshot_row(team, x, y, status="alive", hp=100, gap=0):
    def leaf(value, g=gap):
        return {"value": value, "observed_at": 0, "gap": g}

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


class SquadRowsTest(unittest.TestCase):
    def test_squads_with_no_standing_player_are_kept_and_flagged(self):
        snap = [
            snapshot_row(1, 0, 0),
            snapshot_row(1, 1, 0, status="downed"),
            snapshot_row(2, 10, 0, status="dead"),
            snapshot_row(2, 11, 0, status="eliminated"),
        ]
        rows = {row["team"]: row["features"] for row in squad_rows(snap)}
        self.assertEqual(set(rows), {1, 2})
        self.assertEqual(rows[1]["no_standing"], 0.0)
        self.assertEqual(rows[1]["players_downed"], 1.0)
        self.assertEqual(rows[2]["no_standing"], 1.0)
        self.assertEqual(rows[2]["players_out"], 2.0)
        self.assertIsNone(rows[2]["hp_frac"])
        self.assertEqual(rows[2]["center_dist_missing"], 1.0)
        self.assertEqual(set(rows[1]), set(FEATURES))

    def test_unknown_status_is_counted_not_guessed(self):
        rows = {row["team"]: row["features"] for row in squad_rows([snapshot_row(1, 0, 0, status=None)])}
        self.assertEqual(rows[1]["players_status_unknown"], 1.0)
        self.assertEqual(rows[1]["no_standing"], 1.0)

    def test_unknown_health_stays_unknown(self):
        rows = {row["team"]: row["features"] for row in squad_rows([snapshot_row(1, 0, 0, hp=None), snapshot_row(2, 5, 0)])}
        self.assertIsNone(rows[1]["hp_frac"])
        self.assertEqual(rows[1]["hp_frac_missing"], 1.0)
        self.assertAlmostEqual(rows[2]["hp_frac"], 1.0)

    def test_distances_are_relative_within_the_snapshot(self):
        snap = [snapshot_row(1, 0, 0), snapshot_row(2, 10, 0), snapshot_row(3, 1000, 0)]
        scaled = [snapshot_row(1, 0, 0), snapshot_row(2, 100, 0), snapshot_row(3, 10000, 0)]
        a = {row["team"]: row["features"]["center_dist_rel"] for row in squad_rows(snap)}
        b = {row["team"]: row["features"]["center_dist_rel"] for row in squad_rows(scaled)}
        for team in a:
            self.assertAlmostEqual(a[team], b[team])
        self.assertGreater(a[3], a[1])

    def test_carried_position_counts_as_stale(self):
        snap = [snapshot_row(1, 0, 0, gap=0), snapshot_row(1, 1, 0, gap=31), snapshot_row(2, 9, 0)]
        rows = {row["team"]: row["features"] for row in squad_rows(snap)}
        self.assertAlmostEqual(rows[1]["stale_pos_frac"], 0.5)


class CheckpointTest(unittest.TestCase):
    def test_fixed_30_second_grid_stops_before_teams_alive_reaches_one(self):
        self.assertEqual(sorted(replay_checkpoints(synthetic_game(decided_at=100)["series"])["snapshots"]), [10, 40, 70])
        self.assertEqual(sorted(replay_checkpoints(synthetic_game(decided_at=101)["series"])["snapshots"]), [10, 40, 70, 100])

    def test_leaving_playing_also_stops_the_grid(self):
        payload = synthetic_game(decided_at=200)
        payload["series"].insert(-2, {"t": 130, "d": {"state": ["Playing", "WinnerDetermined"]}})
        payload["series"][-2]["d"] = {"tail": [1]}
        result = replay_checkpoints(payload["series"])
        self.assertEqual(result["cutoff"], 130)
        self.assertEqual(sorted(result["snapshots"]), [10, 40, 70, 100])

    def test_earlier_checkpoints_do_not_depend_on_match_length(self):
        short = replay_checkpoints(synthetic_game(decided_at=100)["series"])
        long = replay_checkpoints(synthetic_game(decided_at=400)["series"])
        for t in (10, 40, 70):
            self.assertEqual(short["snapshots"][t], long["snapshots"][t])
        self.assertEqual(len(long["snapshots"]), 13)

    def test_rows_do_not_see_writes_after_their_checkpoint(self):
        early = replay_checkpoints(synthetic_game(out_at=45)["series"])
        late = replay_checkpoints(synthetic_game(out_at=75)["series"])
        self.assertEqual(early["snapshots"][40], late["snapshots"][40])
        self.assertNotEqual(early["snapshots"][70], late["snapshots"][70])

    def test_snapshots_match_the_decoder_replay(self):
        series = synthetic_game()["series"]
        ours = replay_checkpoints(series)
        theirs = replay(series, set(ours["snapshots"]))
        self.assertEqual(ours["snapshots"], theirs["snapshots"])
        self.assertEqual(ours["state"], theirs["state"])


class LabelTest(unittest.TestCase):
    def test_every_group_contains_the_verified_winner(self):
        game = game_examples(synthetic_game(winner=2), record(1, winner=2), standings(1, winner=2))
        self.assertEqual(game["winner"], 2)
        self.assertEqual([group["t"] for group in game["groups"]], [10, 40, 70])
        for group in game["groups"]:
            self.assertEqual(group["rows"][group["label"]]["team"], 2)
            self.assertEqual(len(group["rows"]), 3)
        self.assertEqual(game["groups"][2]["elapsed"], 60)

    def test_winner_without_standing_players_is_kept_and_reported(self):
        payload = synthetic_game(winner=1, winner_out=((35, "dead"), (60, "alive")))
        game = game_examples(payload, record(1), standings(1))
        self.assertEqual(len(game["groups"]), 3)
        self.assertEqual(game["diagnostics"]["winner_no_standing"], [40])

    def test_label_without_standings_check_is_not_used(self):
        game = game_examples(synthetic_game(), record(1, source="replay.teamsAlive"), standings(1))
        self.assertEqual(game["groups"], [])
        self.assertEqual(game["skipped"][0]["reason"], "label_not_verified_against_standings")

    def test_cross_check_must_agree_with_the_manifest(self):
        game = game_examples(synthetic_game(winner=1), record(1, winner=3), standings(1, winner=1))
        self.assertEqual(game["groups"], [])
        self.assertEqual(game["skipped"][0]["reason"], "winner_cross_check_failed")
        missing = game_examples(synthetic_game(), record(1), None)
        self.assertEqual(missing["skipped"][0]["reason"], "standings_missing_for_cross_check")


def write_dataset(data: Path, games: list[dict]) -> None:
    """``games``: dicts with game_id, session_id, winner, match_start, and payload."""
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "collection" / "cache").mkdir(parents=True, exist_ok=True)
    manifest = {}
    boards: dict = {}
    for game in games:
        live = f"live{game['game_id']}"
        (data / "raw" / f"{live}.timeseries.json").write_text(json.dumps(game["payload"]))
        manifest[str(game["game_id"])] = record(
            game["game_id"],
            winner=game["winner"],
            session_id=game["session_id"],
            livedata_id=live,
            sha256=f"h{game['game_id']}",
            match_start=game.get("match_start"),
        )
        boards.setdefault(game["session_id"], []).append(standings(game["game_id"], winner=game["winner"]))
    for session, items in boards.items():
        (data / "collection" / "cache" / f"stats-{session}.json").write_text(json.dumps({"games": items}))
    (data / "collection" / "manifest.json").write_text(json.dumps({"games": manifest}))


class LoadAcceptedTest(unittest.TestCase):
    def test_inventory_and_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_dataset(data, [{"game_id": 1, "session_id": 9, "winner": 1, "payload": synthetic_game()}])
            path = data / "collection" / "manifest.json"
            manifest = json.loads(path.read_text())
            games = manifest["games"]
            games["2"] = copy.deepcopy(games["1"]) | {"game_id": 2}
            games["3"] = record(3, livedata_id="nope", sha256="h3")
            games["4"] = record(4, source="replay.teamsAlive", livedata_id="live1")
            games["5"] = copy.deepcopy(games["1"]) | {"game_id": 5, "acceptance_status": "excluded"}
            path.write_text(json.dumps(manifest))
            records, inventory = load_accepted(data)
            self.assertEqual([r["game_id"] for r in records], [1])
            self.assertEqual(inventory["accepted_in_manifest"], 4)
            self.assertEqual(inventory["duplicate_payloads"], [{"game_id": 2, "same_as": 1}])
            self.assertEqual(inventory["accepted_missing_raw"], [3])
            self.assertEqual(inventory["accepted_label_unverified"], [4])
            groups, dropped, diagnostics = build_dataset(records, data)
            self.assertEqual(len(groups), 3)
            self.assertEqual(dropped, [])
            self.assertEqual(diagnostics["1"]["checkpoints"], 3)

    def test_extraction_cli_writes_a_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_dataset(
                data,
                [{"game_id": 1, "session_id": 9, "winner": 1, "payload": synthetic_game(winner_out=((35, "dead"), (60, "alive")))}],
            )
            with mock.patch("builtins.print"):
                code = features.main(["--data-dir", str(data)])
            summary = json.loads((data / "models" / "extraction.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(summary["checkpoint_groups"], 3)
        self.assertEqual(summary["winner_no_standing_checkpoints"], 1)
        self.assertEqual(summary["games_with_winner_no_standing"], 1)

    def test_missing_manifest_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            records, inventory = load_accepted(Path(tmp))
        self.assertEqual(records, [])
        self.assertFalse(inventory["manifest_exists"])


if __name__ == "__main__":
    unittest.main()
