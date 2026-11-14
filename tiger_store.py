"""Dashboard store backed by Tiger Data through ``TigerReader``.

Same interface as ``dashboard_data.MatchStore`` so the routes and frontend do
not change. Held-out and final-model predictions come from separate
``prediction_kind`` rows and are never mixed. A view with no stored rows says
"Not available"; nothing is filled in from local files.
"""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from dashboard_data import (
    COORDINATE_NOTE,
    HELDOUT_PREDICTION_TYPE,
    PALETTE,
    _unavailable,
    checkpoint_at,
    constrained_metrics,
)


def _iso(value) -> str | None:
    """UTC ISO time. Postgres returns timestamptz in the session time zone."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _version(text: str | None) -> dict:
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}


class TigerStore:
    backend = "tiger"

    def __init__(self, reader):
        self.reader = reader
        self._times: dict[Any, list[dict]] = {}
        self._views: dict[tuple, dict] = {}
        self._squads: dict[Any, list[dict]] = {}

    # --- shared pieces -------------------------------------------------------

    def matches(self) -> list[dict]:
        return self.reader.list_matches()

    def _squad_list(self, game_id: int) -> list[dict]:
        if game_id not in self._squads:
            rows = self.reader.list_squads(game_id)
            self._squads[game_id] = [
                {
                    "teamId": row["team_id"],
                    "teamName": row["team_name"] or f"Team {row['team_id']}",
                    "color": PALETTE[index % len(PALETTE)],
                }
                for index, row in enumerate(sorted(rows, key=lambda r: str(r["team_id"])))
            ]
        return self._squads[game_id]

    def _frame_times(self, game_id: int) -> list[dict]:
        if game_id not in self._times:
            self._times[game_id] = self.reader.frame_times(game_id)
        return self._times[game_id]

    def _points(self, game_id: int, kind: str) -> tuple[list[dict], dict]:
        names = {squad["teamId"]: squad["teamName"] for squad in self._squad_list(game_id)}
        points: dict[int, dict] = {}
        version: dict = {}
        for row in self.reader.estimates(game_id, kind):
            version = version or _version(row["model_version"])
            point = points.setdefault(
                row["t"],
                {
                    "t": row["t"],
                    "elapsed": row["elapsed_s"],
                    "replay_time_utc": _iso(row["ts"]),
                    "elimination_verified": row["elimination_verified"],
                    "eligibility": "verified" if row["elimination_verified"] else "unverified",
                    "eligibility_reasons": [],
                    "squads": [],
                },
            )
            point["squads"].append(
                {
                    "teamId": row["team_id"],
                    "team_name": names.get(row["team_id"]),
                    "eliminated": row["eliminated"],
                    "model": row["model_probability"],
                    "model_unconstrained": row["model_probability_raw"],
                    "uniform_contention": row["baseline_probability"],
                }
            )
        ordered = [points[t] for t in sorted(points)]
        for point in ordered:
            point["in_contention"] = sum(1 for squad in point["squads"] if not squad["eliminated"])
            point["baseline"] = "uniform_contention" if point["elimination_verified"] else "uniform_lobby_fallback"
        return ordered, version

    # --- views -----------------------------------------------------------------

    def heldout(self, game_id: int) -> dict:
        key = ("heldout", game_id)
        if key not in self._views:
            match = self.reader.match(game_id) or {}
            points, version = self._points(game_id, "held_out")
            if not points:
                split = match.get("evaluation_split")
                self._views[key] = _unavailable(
                    "Held-out predictions are not available for this game"
                    + (f": it is in the {split} sessions, which were used to fit or select the evaluation model."
                       if split in ("train", "validation") else ": the database holds no held_out rows for it."),
                    split=split,
                )
            else:
                self._views[key] = {
                    "available": True,
                    "message": None,
                    "prediction_type": version.get("prediction_type") or HELDOUT_PREDICTION_TYPE,
                    "held_out": True,
                    "split": match.get("evaluation_split"),
                    "source": version.get("source"),
                    "code_commit": version.get("code_commit"),
                    "evaluation_model": {"fit_on": version.get("fit_on"), "l2": version.get("l2")},
                    "game_metrics": None,
                    "baseline": "uniform_contention",
                    "points": points,
                    "limitation": (
                        "Held-out: made by a model fit on other tournament sessions (read from Tiger Data, "
                        "imported from the verified export bundle). Predictions exist every 30 s; between "
                        "checkpoints the most recent past prediction is shown with its own time."
                    ),
                }
        return self._views[key]

    def curve(self, game_id: int) -> dict:
        key = ("final", game_id)
        if key not in self._views:
            points, version = self._points(game_id, "final_model_fitted")
            if not points:
                self._views[key] = _unavailable("The database holds no final_model_fitted rows for this game.",
                                                prediction_type=None)
            else:
                in_sample = version.get("in_trained_on_games")
                self._views[key] = {
                    "available": True,
                    "message": None,
                    "prediction_type": "final_model_fitted",
                    "held_out": False,
                    "in_sample_game": in_sample,
                    "model_version": {k: version.get(k) for k in ("l2", "checkpoint_step_s", "features", "trained_on_game_count")},
                    "baseline": "uniform_contention",
                    "points": points,
                    "limitation": (
                        "Fitted on these games: the final model was trained on every accepted game"
                        + (", including this one" if in_sample else "")
                        + ". This is not held-out evidence. A rise or fall is not a causal effect of a decision."
                    ),
                }
        return self._views[key]

    @staticmethod
    def _header(view: dict) -> dict:
        return {key: value for key, value in view.items() if key != "points"} | {
            "point_count": len(view.get("points") or [])
        }

    def summary(self, game_id: int) -> dict | None:
        match = self.reader.match(game_id)
        if match is None:
            return None
        times = self._frame_times(game_id)
        return {
            "game_id": game_id,
            "session_id": match["session_id"],
            "event": match["event"],
            "map_name": match["map_name"],
            "quality_score": match["quality_score"],
            "organizer_username": match["organizer_username"],
            "winner": {
                "teamId": match["winner_team_id"],
                "teamName": match["winner_team_name"],
                "label_source": match["winner_label_source"],
            },
            "playing_start": match["playing_start_s"],
            "frame_count": len(times),
            "times": times,
            "squads": self._squad_list(game_id),
            "coordinate_note": COORDINATE_NOTE,
            "heldout": self._header(self.heldout(game_id)),
            "curve": self._header(self.curve(game_id)),
        }

    def frame(self, game_id: int, index: int) -> dict | None:
        times = self._frame_times(game_id)
        if not times:
            return None
        index = max(0, min(index, len(times) - 1))
        stored = self.reader.frame_at_or_before(game_id, times[index]["t"])
        if stored is None:
            return None
        names = {squad["teamId"]: squad["teamName"] for squad in self._squad_list(game_id)}
        players = [
            {
                "id": row["player_id"],
                "name": row["player_name"],
                "teamId": row["team_id"],
                "teamName": names.get(row["team_id"]),
                "x": row["x"],
                "y": row["y"],
                "health": row["health"],
                "healthMax": row["health_max"],
                "shields": row["shields"],
                "shieldMax": row["shield_max"],
                "status": row["status"],
            }
            for row in stored["players"]
        ]
        elapsed = stored["elapsed_s"]
        selected = {}
        for name, view in (("heldout", self.heldout(game_id)), ("final", self.curve(game_id))):
            selected[name] = checkpoint_at(view.get("points") or [], elapsed) if view.get("available") else None
        return {
            "index": index,
            "squads": self._squad_list(game_id),
            "checkpoints": selected,
            "t": stored["t"],
            "elapsed": elapsed,
            "players": players,
        }

    def curve_points(self, game_id: int) -> dict:
        return self.curve(game_id)

    def heldout_points(self, game_id: int) -> dict:
        return self.heldout(game_id)

    def evaluation(self) -> dict:
        row = self.reader.evaluation_report()
        if row is None:
            return {"available": False, "message": "Not available", "reason": "No evaluation report in the database."}
        return {
            "available": True,
            "message": None,
            "prediction_type": row["method"],
            "held_out": row["held_out"],
            "warning": None,
            "games": {"test": row["test_games"]},
            "metrics": constrained_metrics(json.loads(row["metrics_json"])) if row["metrics_json"] else {},
            "calibration": {"status": "not stored in the database"},
            "generated_at": _iso(row["generated_at"]) if hasattr(row["generated_at"], "isoformat") else row["generated_at"],
            "limitation": row["note"],
        }
