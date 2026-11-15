"""Dashboard contract: one replay, no invented probabilities."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from dashboard_data import MatchStore, build_timeline, evaluation_panel, prediction_curve


def _series():
    player = {
        "name": "A",
        "teamId": 1,
        "teamName": "Red",
        "pos": {"x": 10, "y": 20, "z": 1},
        "currentHealth": 80,
        "maxHealth": 100,
        "shieldHealth": 25,
        "shieldMaxHealth": 50,
        "status": "alive",
        "nucleusHash": "a",
    }
    other = {
        "name": "B",
        "teamId": 2,
        "teamName": "Blue",
        "pos": {"x": 40, "y": 5, "z": 1},
        "currentHealth": 50,
        "maxHealth": 100,
        "shieldHealth": 0,
        "shieldMaxHealth": 50,
        "status": "downed",
        "nucleusHash": "b",
    }
    return [
        {"t": 0, "d": {"state": ["Playing"], "teamsAlive": [2], "players": [{"a": player, "b": other}]}, "f": []},
        {"t": 1, "d": {"players": {"a": {"pos": {"x": [10, 12]}}}}, "f": []},
        {"t": 2, "d": {"state": ["Playing", "WinnerDetermined"], "teamsAlive": [2, 1]}, "f": []},
    ]


class TimelineTest(unittest.TestCase):
    def test_one_pass_keeps_playing_frames_and_omits_placement(self):
        timeline = build_timeline(_series())
        self.assertEqual(timeline["playing_start"], 0)
        self.assertEqual([frame["elapsed"] for frame in timeline["frames"]], [0, 1])
        player = timeline["frames"][1]["players"][0]
        self.assertEqual(player["x"], 12)
        self.assertNotIn("teamPlacement", player)
        self.assertEqual(timeline["frames"][0]["players"][1]["status"], "downed")

    def test_missing_model_and_report_are_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            curve = prediction_curve([{"t": 0, "elapsed": 0, "rows": []}], data, 1)
            panel = evaluation_panel(data)
        self.assertFalse(curve["available"])
        self.assertEqual(curve["message"], "Not available")
        self.assertEqual(curve["prediction_type"], None)
        self.assertFalse(panel["available"])
        self.assertEqual(panel["message"], "Not available")


class ApiTest(unittest.TestCase):
    def test_selector_and_frame_use_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "collection").mkdir()
            (data / "raw").mkdir()
            raw = data / "raw" / "demo.timeseries.json"
            raw.write_text(json.dumps({"series": _series()}), encoding="utf-8")
            record = {
                "acceptance_status": "accepted",
                "game_id": 7,
                "session_id": 3,
                "event": "Demo Cup",
                "map_name": "mp_rr_district_mu1",
                "quality_score": 8.5,
                "match_start": 10,
                "livedata_id": "demo",
                "download_path": raw.as_posix(),
                "validation": {"winner": {"teamId": 1, "teamName": "Red", "label_source": "test"}},
            }
            (data / "collection" / "manifest.json").write_text(
                json.dumps({"games": {"7": record}}),
                encoding="utf-8",
            )
            app = create_app(data)
            client = app.test_client()
            matches = client.get("/api/matches").get_json()
            self.assertEqual(matches[0]["event"], "Demo Cup")
            self.assertEqual(matches[0]["quality_score"], 8.5)
            summary = client.get("/api/matches/7").get_json()
            self.assertGreaterEqual(summary["frame_count"], 2)
            self.assertIn("unverified", summary["coordinate_note"])
            self.assertFalse(summary["curve"]["available"])
            first = client.get("/api/matches/7/frames/0").get_json()
            second = client.get("/api/matches/7/frames/1").get_json()
            self.assertEqual(second["players"][0]["x"], 12)
            # Seeks are by replay second; a second past the end returns the last frame.
            self.assertEqual((summary["first_t"], summary["last_t"]), (0, 1))
            self.assertEqual(client.get("/api/matches/7/at/1").get_json()["t"], 1)
            self.assertEqual(client.get("/api/matches/7/at/99").get_json()["t"], 1)
            self.assertTrue((data / "dashboard" / "7.json").is_file())
            store = MatchStore(data)
            self.assertEqual(store.frame(7, 0)["t"], first["t"])
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Squad", page.data)
            evaluation = client.get("/api/evaluation").get_json()
            self.assertEqual(evaluation["message"], "Not available")
            self.assertFalse(summary["heldout"]["available"])
            self.assertEqual(summary["heldout"]["message"], "Not available")
            self.assertIsNone(first["checkpoints"]["heldout"])



# --- held-out integration -----------------------------------------------------

import hashlib

from dashboard_data import (
    HELDOUT_BUNDLE,
    HELDOUT_PREDICTION_TYPE,
    checkpoint_at,
    heldout_view,
    load_heldout_bundle,
)
from features import FEATURES


def _player(team, slot, x):
    return {
        "name": f"p{team}{slot}",
        "teamId": team,
        "teamName": f"T{team}",
        "pos": {"x": x, "y": team * 10.0, "z": 0},
        "currentHealth": 100,
        "maxHealth": 100,
        "shieldHealth": 50,
        "shieldMaxHealth": 50,
        "status": "alive",
        "nucleusHash": f"h{team}{slot}",
    }


def _long_series(decided_at=95):
    """Three squads, Playing from t 0. Squad 3 is eliminated at t 40 (event + teamsAlive)."""
    players = {f"h{team}{slot}": _player(team, slot, team * 100.0 + slot) for team in (1, 2, 3) for slot in (0, 1)}
    out = {f"h3{slot}": {"status": ["alive", "eliminated"]} for slot in (0, 1)}
    event = {"type": "eliminated", "timestamp": "0", "team": [{"teamId": 3, "name": "p30"}, {"teamId": 3, "name": "p31"}]}
    return [
        {"t": 0, "d": {"state": ["Playing"], "match_start": ["1000"], "totalTeams": [3], "teamsAlive": [3], "players": [players]}, "f": []},
        {"t": 40, "d": {"players": out, "teamsAlive": [3, 2]}, "f": [event]},
        {"t": decided_at, "d": {"teamsAlive": [2, 1]}, "f": []},
        {"t": decided_at + 20, "d": {"state": ["Playing", "WinnerDetermined"]}, "f": []},
        {"t": decided_at + 21, "d": {"tail": [1]}, "f": []},
    ]


def _entry(game_id, t, teams=(1, 2, 3), eliminated=()):
    squads = []
    for team in teams:
        squads.append(
            {
                "team_id": team,
                "team_name": f"T{team}",
                "is_winner": team == 1,
                "eliminated": team in eliminated,
                "in_contention": team not in eliminated,
                "standing_players": 2,
                "probabilities": {
                    "model_constrained": 0.0 if team in eliminated else (0.6 if team == 1 else 0.4 / (len(teams) - len(eliminated) - 1)),
                    "model": 0.5 if team == 1 else 0.25,
                    "uniform_contention": 0.0 if team in eliminated else 1 / (len(teams) - len(eliminated)),
                    "uniform": 1 / len(teams),
                    "most_players": 1 / len(teams),
                },
            }
        )
    return {
        "game_id": game_id,
        "session_id": 3,
        "split": "test",
        "t": t,
        "elapsed_s": t,
        "replay_time_unix": 1000 + t,
        "replay_time_utc": f"t{t}",
        "teams_alive": 3 - len(eliminated),
        "lobby_size": len(teams),
        "in_contention": len(teams) - len(eliminated),
        "elimination_verified": True,
        "eligibility": "verified",
        "eligibility_reasons": [],
        "winner_team_id": 1,
        "squads": squads,
    }


def _write_bundle(data: Path, entries, games, **overrides):
    root = data / "exports" / HELDOUT_BUNDLE
    root.mkdir(parents=True, exist_ok=True)
    body = json.dumps(entries).encode()
    (root / "heldout_predictions.json").write_bytes(body)
    meta = {
        "schema_version": 2,
        "prediction_type": HELDOUT_PREDICTION_TYPE,
        "held_out": True,
        "final_model_predictions_included": False,
        "files": {"heldout_predictions.json": {"sha256": hashlib.sha256(body).hexdigest()}},
        "frozen_config": {"games": games, "sessions": {"train": [1], "validation": [2], "test": [3]}},
        "evaluation_model": {"fit_on": "train + validation sessions", "l2": 0.01, "optimizer": {"converged": True}},
    }
    meta.update(overrides)
    (root / "bundle.json").write_text(json.dumps(meta))
    return root


def _write_game(data: Path, game_id=7, series=None):
    (data / "collection").mkdir(parents=True, exist_ok=True)
    (data / "raw").mkdir(parents=True, exist_ok=True)
    raw = data / "raw" / "demo.timeseries.json"
    raw.write_text(json.dumps({"series": series or _long_series()}))
    record = {
        "acceptance_status": "accepted",
        "game_id": game_id,
        "session_id": 3,
        "livedata_id": "demo",
        "download_path": raw.as_posix(),
        "validation": {"winner": {"teamId": 1, "teamName": "T1", "label_source": "test"}},
    }
    (data / "collection" / "manifest.json").write_text(json.dumps({"games": {str(game_id): record}}))


class CheckpointSelectionTest(unittest.TestCase):
    def test_never_selects_a_future_checkpoint(self):
        points = [{"t": 10 + e, "elapsed": e} for e in (0, 30, 60)]
        self.assertIsNone(checkpoint_at(points, -1))
        for elapsed in range(0, 120):
            chosen = checkpoint_at(points, elapsed)
            self.assertLessEqual(chosen["elapsed"], elapsed)
            later = [p for p in points if p["elapsed"] <= elapsed]
            self.assertEqual(chosen["elapsed"], later[-1]["elapsed"])
            self.assertEqual(chosen["age_s"], elapsed - chosen["elapsed"])
        self.assertEqual(checkpoint_at(points, 29)["index"], 0)
        self.assertEqual(checkpoint_at(points, 30)["index"], 1)

    def test_checkpoints_stop_once_the_game_is_decided(self):
        timeline = build_timeline(_long_series(decided_at=95))
        self.assertEqual([c["elapsed"] for c in timeline["checkpoints"]], [0, 30, 60, 90])
        late = build_timeline(_long_series(decided_at=60))
        self.assertEqual([c["elapsed"] for c in late["checkpoints"]], [0, 30])
        self.assertGreater(len(late["frames"]), 61)

    def test_frames_reach_the_recorded_conclusion_without_new_checkpoints(self):
        timeline = build_timeline(_long_series(decided_at=95))
        self.assertEqual(timeline["conclusion_t"], 115)
        self.assertEqual(timeline["frames"][-1]["t"], 115)
        self.assertTrue(timeline["frames"][-1]["finished"])
        self.assertFalse(any(frame["finished"] for frame in timeline["frames"] if frame["t"] < 115))
        self.assertEqual([c["elapsed"] for c in timeline["checkpoints"]], [0, 30, 60, 90])

    def test_no_checkpoint_after_playing_ends_even_if_teams_alive_is_unknown(self):
        series = _long_series(decided_at=95)
        series[2] = {"t": 50, "d": {"state": ["Playing", "WinnerDetermined"]}, "f": []}
        series[3] = {"t": 100, "d": {"tail": [1]}, "f": []}
        series[4] = {"t": 140, "d": {"tail": [2]}, "f": []}
        timeline = build_timeline(series)
        self.assertEqual([c["elapsed"] for c in timeline["checkpoints"]], [0, 30])
        self.assertEqual(timeline["conclusion_t"], 50)
        self.assertEqual(timeline["frames"][-1]["t"], 139)


class BundleTest(unittest.TestCase):
    def test_missing_bundle_is_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = load_heldout_bundle(Path(tmp))
        self.assertFalse(bundle["available"])
        view = heldout_view(bundle, 7, {"checkpoints": []})
        self.assertEqual(view["message"], "Not available")

    def test_tampered_or_mislabeled_bundles_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            root = _write_bundle(data, [_entry(7, 0)], {"test": [7]})
            self.assertTrue(load_heldout_bundle(data)["available"])
            (root / "heldout_predictions.json").write_text("[]")
            self.assertIn("sha256", load_heldout_bundle(data)["reason"])
            _write_bundle(data, [_entry(7, 0)], {"test": [7]}, final_model_predictions_included=True)
            self.assertFalse(load_heldout_bundle(data)["available"])
            _write_bundle(data, [_entry(7, 0)], {"test": [7]}, prediction_type="final_model_fitted")
            self.assertFalse(load_heldout_bundle(data)["available"])


class JoinTest(unittest.TestCase):
    def setUp(self):
        self.timeline = build_timeline(_long_series())

    def _bundle(self, entries, games=None):
        with tempfile.TemporaryDirectory() as tmp:
            _write_bundle(Path(tmp), entries, games or {"test": [7], "train": [8]})
            return load_heldout_bundle(Path(tmp))

    def test_join_on_t_and_team_id(self):
        entries = [_entry(7, 0), _entry(7, 30, eliminated=(3,)), _entry(7, 60, eliminated=(3,)), _entry(7, 90, eliminated=(3,))]
        view = heldout_view(self._bundle(entries), 7, self.timeline)
        self.assertTrue(view["available"], view.get("reason"))
        self.assertTrue(view["held_out"])
        self.assertEqual([p["t"] for p in view["points"]], [0, 30, 60, 90])
        squad3 = [s for s in view["points"][1]["squads"] if s["teamId"] == 3][0]
        self.assertTrue(squad3["eliminated"])
        self.assertEqual(squad3["uniform_contention"], 0.0)
        # The displayed model probability is the bundle's constrained value, not the raw one.
        self.assertEqual(squad3["model"], 0.0)
        self.assertEqual(squad3["model_unconstrained"], 0.25)
        self.assertEqual(view["points"][1]["eligibility"], "verified")

    def test_join_failures_are_not_shown(self):
        wrong_team = heldout_view(self._bundle([_entry(7, 0, teams=(1, 2, 4))]), 7, self.timeline)
        self.assertFalse(wrong_team["available"])
        self.assertIn("squad ids differ", wrong_team["reason"])
        off_grid = heldout_view(self._bundle([_entry(7, 15)]), 7, self.timeline)
        self.assertFalse(off_grid["available"])
        self.assertIn("not a dashboard checkpoint", off_grid["reason"])
        shifted = _entry(7, 30)
        shifted["elapsed_s"] = 31
        self.assertFalse(heldout_view(self._bundle([shifted]), 7, self.timeline)["available"])

    def test_training_games_say_held_out_is_unavailable(self):
        view = heldout_view(self._bundle([_entry(7, 0)]), 8, self.timeline)
        self.assertFalse(view["available"])
        self.assertIn("train sessions", view["reason"])
        unknown = heldout_view(self._bundle([_entry(7, 0)]), 99, self.timeline)
        self.assertIn("no frozen split", unknown["reason"])


class FinalCurveTest(unittest.TestCase):
    def _model(self, data):
        (data / "models").mkdir(parents=True)
        scaler = {name: {"mean": 0.0, "std": 1.0} for name in FEATURES}
        model = {"features": list(FEATURES), "weights": [0.5] + [0.0] * (len(FEATURES) - 1), "scaler": scaler,
                 "l2": 0.01, "trained_on_games": [7], "optimizer": {"converged": True}}
        (data / "models" / "model.json").write_text(json.dumps(model))

    def test_final_curve_uses_verified_contention_baseline(self):
        timeline = build_timeline(_long_series())
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._model(data)
            curve = prediction_curve(timeline["checkpoints"], data, 7, winner=1, replay_match_start="1000")
        self.assertEqual(curve["prediction_type"], "final_model_fitted")
        self.assertFalse(curve["held_out"])
        self.assertTrue(curve["in_sample_game"])
        self.assertIn("Fitted on these games", curve["limitation"])
        late = curve["points"][2]  # t 60, after the t 40 elimination
        self.assertTrue(late["elimination_verified"])
        self.assertEqual(late["baseline"], "uniform_contention")
        by_team = {s["teamId"]: s for s in late["squads"]}
        self.assertEqual(by_team[3]["uniform_contention"], 0.0)
        self.assertEqual(by_team[1]["uniform_contention"], 0.5)
        # Final-model inference applies the same eligibility rule.
        self.assertEqual(by_team[3]["model"], 0.0)
        self.assertGreater(by_team[3]["model_unconstrained"], 0.0)
        self.assertAlmostEqual(sum(s["model"] for s in late["squads"]), 1.0)
        self.assertEqual(late["eligibility"], "verified")
        self.assertEqual(late["replay_time_utc"], "1970-01-01T00:17:40Z")
        before = {s["teamId"]: s for s in curve["points"][1]["squads"]}
        self.assertFalse(before[3]["eliminated"])

    def test_unverified_elimination_falls_back_to_lobby_uniform(self):
        timeline = build_timeline(_long_series())
        for checkpoint in timeline["checkpoints"]:
            checkpoint["meta"]["teamsAlive"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self._model(data)
            curve = prediction_curve(timeline["checkpoints"], data, 7, winner=1)
        late = curve["points"][2]  # t 60, after the t 40 elimination
        self.assertFalse(late["elimination_verified"])
        self.assertEqual(late["eligibility"], "unverified")
        self.assertIn("teamsAlive_disagrees", late["eligibility_reasons"])
        # Unverified eligibility: nothing excluded, probabilities unconstrained.
        for squad in late["squads"]:
            self.assertEqual(squad["model"], squad["model_unconstrained"])
        self.assertEqual(late["baseline"], "uniform_lobby_fallback")
        self.assertEqual({s["uniform_contention"] for s in late["squads"]}, {1 / 3})


class HeldOutApiTest(unittest.TestCase):
    def test_held_out_is_primary_and_separate_from_the_final_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _write_game(data)
            entries = [_entry(7, 0), _entry(7, 30, eliminated=(3,)), _entry(7, 60, eliminated=(3,)), _entry(7, 90, eliminated=(3,))]
            _write_bundle(data, entries, {"test": [7]})
            client = create_app(data).test_client()
            matches = client.get("/api/matches").get_json()
            self.assertTrue(matches[0]["heldout"])
            self.assertEqual(matches[0]["split"], "test")
            summary = client.get("/api/matches/7").get_json()
            self.assertTrue(summary["heldout"]["available"])
            self.assertEqual(summary["heldout"]["prediction_type"], HELDOUT_PREDICTION_TYPE)
            # No model.json here: the final-model view is unavailable, held-out still works.
            self.assertFalse(summary["curve"]["available"])
            for index, expected in ((0, 0), (29, 0), (30, 30), (59, 30), (95, 90)):
                frame = client.get(f"/api/matches/7/frames/{index}").get_json()
                chosen = frame["checkpoints"]["heldout"]
                self.assertEqual(chosen["elapsed"], expected, index)
                self.assertLessEqual(chosen["elapsed"], frame["elapsed"])
                self.assertIsNone(frame["checkpoints"]["final"])
            points = client.get("/api/matches/7/heldout").get_json()["points"]
            self.assertEqual(len(points), 4)



class BackendSelectionTest(unittest.TestCase):
    def test_files_requested_is_labeled_files(self):
        from app import select_store

        with tempfile.TemporaryDirectory() as tmp:
            store, info = select_store(Path(tmp), "files")
        self.assertEqual(store.backend, "files")
        self.assertEqual(info["backend"], "files")
        self.assertIn("requested", info["detail"])

    def test_auto_without_database_says_why_it_uses_files(self):
        from app import select_store

        with tempfile.TemporaryDirectory() as tmp:
            store, info = select_store(Path(tmp), "auto", dsn="")
        self.assertEqual(info["backend"], "files")
        self.assertIn("database not used: DATABASE_URL is not set", info["detail"])

    def test_auto_with_unreachable_database_redacts_and_uses_files(self):
        from app import select_store

        dsn = "postgresql://user:secret@127.0.0.1:1/none?connect_timeout=1"
        with tempfile.TemporaryDirectory() as tmp:
            _store, info = select_store(Path(tmp), "auto", dsn=dsn)
        self.assertEqual(info["backend"], "files")
        self.assertIn("database not used", info["detail"])
        self.assertNotIn("secret", info["detail"])

    def test_tiger_requested_never_falls_back_silently(self):
        from app import select_store

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            select_store(Path(tmp), "tiger", dsn="")

    def test_backend_is_served_and_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(Path(tmp), backend="files").test_client()
            info = client.get("/api/backend").get_json()
            page = client.get("/").data
        self.assertEqual(info["backend"], "files")
        self.assertIn(b'id="backend"', page)


class MatchOrderTest(unittest.TestCase):
    def test_dropdown_order_uses_stored_quality_then_game_id(self):
        from decimal import Decimal

        from dashboard_data import sort_matches

        rows = [
            {"game_id": 1, "quality_score": 10.001},
            {"game_id": 2, "quality_score": 10.004},
            {"game_id": 9, "quality_score": 8.5},
            {"game_id": 4, "quality_score": 8.5},
            {"game_id": 3, "quality_score": 0},
            {"game_id": 8, "quality_score": None},
            {"game_id": 7, "quality_score": "8.50"},
            {"game_id": 6, "quality_score": float("nan")},
            {"game_id": 5, "quality_score": Decimal("9.10")},
            {"game_id": 11, "quality_score": True},
        ]
        self.assertEqual(
            [row["game_id"] for row in sort_matches(rows)],
            [2, 1, 5, 9, 4, 3, 11, 8, 7, 6],
        )
        # 10.004 and 10.001 both display as 10.00; the stored numbers stay in that order.
        self.assertEqual(f"{10.004:.2f}", f"{10.001:.2f}")

    def test_file_and_tiger_lists_share_that_order(self):
        from contextlib import contextmanager

        from tiger_read import TigerReader

        games = {
            "1": {"acceptance_status": "accepted", "game_id": 1, "event": "Low", "map_name": "mp", "quality_score": 7.25, "match_start": 1},
            "30": {"acceptance_status": "accepted", "game_id": 30, "event": "Expansion", "map_name": "mp", "quality_score": 9.5, "match_start": 2},
            "12": {"acceptance_status": "accepted", "game_id": 12, "event": "Same", "map_name": "mp", "quality_score": 9.5, "match_start": 9},
            "4": {"acceptance_status": "accepted", "game_id": 4, "event": "Missing", "map_name": "mp", "quality_score": None, "match_start": 3},
        }
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "collection").mkdir()
            (data / "collection" / "manifest.json").write_text(json.dumps({"games": games}), encoding="utf-8")
            listed = create_app(data, backend="files").test_client().get("/api/matches").get_json()
        self.assertEqual([row["game_id"] for row in listed], [30, 12, 1, 4])
        self.assertEqual([row["quality_score"] for row in listed], [9.5, 9.5, 7.25, None])

        class Result:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class Connection:
            def execute(self, _query):
                return Result([
                    (1, 1, "Low", "mp", 7.25, "org", None, None, 1, "W", "src", False),
                    (30, 1, "Expansion", "mp", 9.5, "org", None, None, 1, "W", "src", False),
                    (12, 1, "Same", "mp", 9.5, "org", None, None, 1, "W", "src", False),
                    (4, 1, "Missing", "mp", None, "org", None, None, 1, "W", "src", False),
                ])

        reader = TigerReader.__new__(TigerReader)

        @contextmanager
        def connection():
            yield Connection()

        reader._connection = connection
        tiger = reader.list_matches()
        self.assertEqual([row["game_id"] for row in tiger], [row["game_id"] for row in listed])
        self.assertEqual([row["event"] for row in tiger], ["Expansion", "Same", "Low", "Missing"])


if __name__ == "__main__":
    unittest.main()
