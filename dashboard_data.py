"""Read collector telemetry for the dashboard. Does not train or alter the model.

Prediction numbers are read from ``data/models/model.json`` and
``data/models/report.json`` when those files exist. They are never invented.
A curve produced from ``model.json`` is a final-model fitted prediction:
``train_model.run`` fits that file on every usable game after the held-out
evaluation. Held-out probabilities are not stored per checkpoint, so the
curve is not a held-out result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from features import squad_rows
from overstat_patch import apply_frame, export_players, frame_entries, initial_state

PALETTE = (
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
    "#86bcb6",
    "#d37295",
    "#8cd17d",
    "#b6992d",
    "#499894",
    "#fabfd2",
    "#d4a6c8",
    "#a0cbe8",
    "#ffbe7d",
    "#c7c7c7",
)

COORDINATE_NOTE = (
    "x and y are the replay's pos coordinates. Their units and map alignment "
    "are unverified, so this is an XY plot, not a map image. z is omitted."
)


def _leaf(row: dict, *path: str) -> Any:
    value = row
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def list_matches(data_dir: Path) -> list[dict]:
    """Accepted games only, oldest ``match_start`` first."""
    manifest = data_dir / "collection" / "manifest.json"
    if not manifest.is_file():
        return []
    document = json.loads(manifest.read_text(encoding="utf-8"))
    rows = []
    for record in (document.get("games") or {}).values():
        if not isinstance(record, dict) or record.get("acceptance_status") != "accepted":
            continue
        winner = (record.get("validation") or {}).get("winner") or {}
        rows.append(
            {
                "game_id": record.get("game_id"),
                "session_id": record.get("session_id"),
                "event": record.get("event"),
                "map_name": record.get("map_name"),
                "quality_score": record.get("quality_score"),
                "match_start": record.get("match_start"),
                "organizer_username": record.get("organizer_username"),
                "winner": {
                    "teamId": winner.get("teamId"),
                    "teamName": winner.get("teamName"),
                    "label_source": winner.get("label_source"),
                },
            }
        )
    rows.sort(key=lambda item: ((item["match_start"] is None), item["match_start"] or 0, str(item["game_id"])))
    return rows


def _record(data_dir: Path, game_id: int) -> dict | None:
    manifest = data_dir / "collection" / "manifest.json"
    if not manifest.is_file():
        return None
    document = json.loads(manifest.read_text(encoding="utf-8"))
    for record in (document.get("games") or {}).values():
        if isinstance(record, dict) and record.get("game_id") == game_id and record.get("acceptance_status") == "accepted":
            return record
    return None


def _raw_path(record: dict, data_dir: Path) -> Path | None:
    if record.get("download_path"):
        candidate = Path(record["download_path"])
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.is_file():
            return candidate
        under_data = data_dir / "raw" / Path(record["download_path"]).name
        if under_data.is_file():
            return under_data
    if record.get("livedata_id"):
        candidate = data_dir / "raw" / f"{record['livedata_id']}.timeseries.json"
        if candidate.is_file():
            return candidate
    return None


def _compact_player(row: dict) -> dict:
    return {
        "id": row.get("nucleusHash"),
        "name": _leaf(row, "name", "value"),
        "teamId": _leaf(row, "teamId", "value"),
        "teamName": _leaf(row, "teamName", "value"),
        "x": _leaf(row, "pos", "value", "x"),
        "y": _leaf(row, "pos", "value", "y"),
        "health": _leaf(row, "health", "current", "value"),
        "healthMax": _leaf(row, "health", "max", "value"),
        "shields": _leaf(row, "shields", "current", "value"),
        "shieldMax": _leaf(row, "shields", "max", "value"),
        "status": _leaf(row, "status", "value"),
    }


def build_timeline(series: list[dict]) -> dict:
    """One decoder pass. Frames cover Playing only, one per replay second."""
    state = initial_state()
    observed: dict = {}
    start = None
    frames = []
    checkpoints = []
    for frame in frame_entries(series):
        t = frame["t"]
        events = frame.get("f") or []
        if not isinstance(events, list):
            events = []
        state, observed, error = apply_frame(state, frame.get("d"), events, observed, t)
        if error:
            continue
        if state.get("state") != "Playing":
            if start is None:
                continue
            break
        if start is None:
            start = t
        exported = export_players(state, observed, t)
        elapsed = t - start
        frames.append(
            {
                "t": t,
                "elapsed": elapsed,
                "players": [_compact_player(row) for row in exported],
            }
        )
        if elapsed % 30 == 0:
            checkpoints.append({"t": t, "elapsed": elapsed, "rows": squad_rows(exported)})
    return {"playing_start": start, "frames": frames, "checkpoints": checkpoints}


def _squads(frames: list[dict]) -> list[dict]:
    names: dict[Any, str | None] = {}
    for frame in frames:
        for player in frame["players"]:
            team_id = player.get("teamId")
            if team_id is None:
                continue
            if team_id not in names and player.get("teamName"):
                names[team_id] = player["teamName"]
            elif team_id not in names:
                names[team_id] = None
    ordered = sorted(names, key=str)
    return [
        {
            "teamId": team_id,
            "teamName": names[team_id] or f"Team {team_id}",
            "color": PALETTE[index % len(PALETTE)],
        }
        for index, team_id in enumerate(ordered)
    ]


def _model_file(data_dir: Path) -> Path:
    return data_dir / "models" / "model.json"


def _report_file(data_dir: Path) -> Path:
    return data_dir / "models" / "report.json"


def prediction_curve(checkpoints: list[dict], data_dir: Path, game_id: Any) -> dict:
    """Fitted probabilities only. Held-out checkpoint probabilities are not stored."""
    path = _model_file(data_dir)
    if not path.is_file() or not checkpoints:
        return {
            "available": False,
            "message": "Not available",
            "prediction_type": None,
            "model_version": None,
            "baseline": None,
            "points": [],
            "limitation": (
                "No model.json with stored weights, and no per-checkpoint held-out "
                "probabilities. The dashboard does not fit a model."
            ),
        }
    model = json.loads(path.read_text(encoding="utf-8"))
    from train_model import players_baseline, predict, uniform_baseline

    trained = set(model.get("trained_on_games") or [])
    in_sample = game_id in trained
    points = []
    for checkpoint in checkpoints:
        group = {"rows": checkpoint["rows"]}
        model_probs = predict(model, group)
        uniform = uniform_baseline(group)
        most = players_baseline(group)
        squads = []
        for index, row in enumerate(checkpoint["rows"]):
            squads.append(
                {
                    "teamId": row["team"],
                    "model": model_probs[index],
                    "uniform": uniform[index],
                    "most_players": most[index],
                }
            )
        points.append({"t": checkpoint["t"], "elapsed": checkpoint["elapsed"], "squads": squads})
    version = {
        "l2": model.get("l2"),
        "checkpoint_step_s": model.get("checkpoint_step_s"),
        "features": model.get("features"),
        "converged": (model.get("optimizer") or {}).get("converged"),
    }
    return {
        "available": True,
        "message": None,
        "prediction_type": "final_model_fitted",
        "held_out": False,
        "in_sample_game": in_sample,
        "model_version": version,
        "baseline": "uniform and most_players, computed from the same checkpoint features",
        "points": points,
        "limitation": (
            "These probabilities come from the final model fit. "
            + (
                "This game is listed in trained_on_games, so the curve is not a held-out prediction."
                if in_sample
                else "This game is not listed in trained_on_games. The curve is still a forward pass of the final fit, not the held-out evaluation."
            )
            + " A rise or fall is not a causal effect of a decision."
        ),
    }


def evaluation_panel(data_dir: Path) -> dict:
    path = _report_file(data_dir)
    if not path.is_file():
        return {
            "available": False,
            "message": "Not available",
            "reason": "data/models/report.json is not in the data directory.",
        }
    report = json.loads(path.read_text(encoding="utf-8"))
    evaluation = report.get("evaluation") or {}
    if not evaluation or evaluation.get("status") != "ok":
        return {
            "available": False,
            "message": "Not available",
            "reason": f"report status is {report.get('status')!r}; evaluation is not an ok held-out result.",
            "report_status": report.get("status"),
        }
    test = evaluation.get("test") or {}
    overall = (test.get("overall") or {}).get("game_weighted") or {}
    return {
        "available": True,
        "message": None,
        "prediction_type": evaluation.get("method"),
        "held_out": evaluation.get("method") == "session_split",
        "warning": evaluation.get("warning"),
        "games": evaluation.get("games"),
        "l2": evaluation.get("l2"),
        "metrics": overall,
        "calibration": test.get("calibration"),
        "generated_at": report.get("generated_at"),
        "limitation": (
            "Metrics are the report's held-out aggregates, not a live effect of the squad or time you select. "
            "Session-split test metrics are held out by session. "
            "game_held_out_weaker shares sessions with training and is labeled as such in the report."
        ),
    }


class MatchStore:
    """Caches one reconstructed timeline per game. Slider reads do not replay."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._memory: dict[Any, dict] = {}

    def _cache_path(self, game_id: Any) -> Path:
        return self.data_dir / "dashboard" / f"{game_id}.json"

    def timeline(self, game_id: int) -> dict | None:
        if game_id in self._memory:
            return self._memory[game_id]
        disk = self._cache_path(game_id)
        if disk.is_file():
            loaded = json.loads(disk.read_text(encoding="utf-8"))
            self._memory[game_id] = loaded
            return loaded
        record = _record(self.data_dir, game_id)
        if record is None:
            return None
        path = _raw_path(record, self.data_dir)
        if path is None:
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        built = build_timeline(payload.get("series") or [])
        squads = _squads(built["frames"])
        curve = prediction_curve(built["checkpoints"], self.data_dir, game_id)
        stored = {
            "game_id": game_id,
            "playing_start": built["playing_start"],
            "coordinate_note": COORDINATE_NOTE,
            "squads": squads,
            "frames": built["frames"],
            "curve": curve,
        }
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text(json.dumps(stored) + "\n", encoding="utf-8")
        self._memory[game_id] = stored
        return stored

    def summary(self, game_id: int) -> dict | None:
        timeline = self.timeline(game_id)
        if timeline is None:
            return None
        record = _record(self.data_dir, game_id) or {}
        winner = (record.get("validation") or {}).get("winner") or {}
        return {
            "game_id": game_id,
            "session_id": record.get("session_id"),
            "event": record.get("event"),
            "map_name": record.get("map_name"),
            "quality_score": record.get("quality_score"),
            "organizer_username": record.get("organizer_username"),
            "winner": {
                "teamId": winner.get("teamId"),
                "teamName": winner.get("teamName"),
                "label_source": winner.get("label_source"),
            },
            "playing_start": timeline["playing_start"],
            "frame_count": len(timeline["frames"]),
            "times": [{"t": frame["t"], "elapsed": frame["elapsed"]} for frame in timeline["frames"]],
            "squads": timeline["squads"],
            "coordinate_note": timeline["coordinate_note"],
            "curve": {
                key: value
                for key, value in timeline["curve"].items()
                if key != "points"
            }
            | {"point_count": len(timeline["curve"].get("points") or [])},
        }

    def frame(self, game_id: int, index: int) -> dict | None:
        timeline = self.timeline(game_id)
        if timeline is None or not timeline["frames"]:
            return None
        index = max(0, min(index, len(timeline["frames"]) - 1))
        frame = timeline["frames"][index]
        return {"index": index, "squads": timeline["squads"], **frame}

    def curve_points(self, game_id: int) -> dict:
        timeline = self.timeline(game_id)
        if timeline is None:
            return {"available": False, "message": "Not available", "points": []}
        return timeline["curve"]
