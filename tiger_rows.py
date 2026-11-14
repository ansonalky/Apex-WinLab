"""Turn file-backed match data into Tiger Data rows.

This module does not open a database and does not change the decoder or model.
Unknown player fields stay None so the loader can insert NULL.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HELD_OUT = "held_out"
FITTED = "final_model_fitted"
BASELINE = "uniform_contention"


def load_record(data_dir: Path, game_id: int) -> dict:
    manifest = data_dir / "collection" / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    for record in (document.get("games") or {}).values():
        if isinstance(record, dict) and record.get("game_id") == game_id and record.get("acceptance_status") == "accepted":
            return record
    raise SystemExit(f"accepted game {game_id} is not in the manifest")


def accepted_ids(data_dir: Path) -> list[int]:
    manifest = data_dir / "collection" / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    ids = []
    for record in (document.get("games") or {}).values():
        if isinstance(record, dict) and record.get("acceptance_status") == "accepted":
            ids.append(int(record["game_id"]))
    return sorted(ids)


def load_timeline(data_dir: Path, game_id: int) -> dict:
    """Prefer the dashboard cache. Otherwise one replay through the existing builder."""
    cache = data_dir / "dashboard" / f"{game_id}.json"
    if cache.is_file():
        timeline = json.loads(cache.read_text(encoding="utf-8"))
        if timeline.get("frames") and timeline.get("playing_start") is not None:
            return timeline
    from dashboard_data import build_timeline

    record = load_record(data_dir, game_id)
    raw = _raw_path(record, data_dir)
    if raw is None:
        raise SystemExit(f"no raw telemetry for game {game_id}")
    payload = json.loads(raw.read_text(encoding="utf-8-sig"))
    return build_timeline(payload.get("series") or [])


def _raw_path(record: dict, data_dir: Path) -> Path | None:
    if record.get("download_path"):
        candidate = Path(record["download_path"])
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.is_file():
            return candidate
    if record.get("livedata_id"):
        candidate = data_dir / "raw" / f"{record['livedata_id']}.timeseries.json"
        if candidate.is_file():
            return candidate
    return None


def replay_timestamp(match_start: int, t: int) -> datetime:
    """Absolute time of replay clock ``t``. ``t`` is seconds after match_start."""
    return datetime.fromtimestamp(int(match_start) + int(t), timezone.utc)


def match_tuple(record: dict, timeline: dict) -> tuple:
    start = record.get("match_start")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        raise SystemExit(f"game {record.get('game_id')} has no numeric match_start")
    winner = (record.get("validation") or {}).get("winner") or {}
    return (
        record.get("game_id"),
        record.get("session_id"),
        record.get("event"),
        record.get("map_name"),
        record.get("quality_score"),
        record.get("organizer_username"),
        datetime.fromtimestamp(int(start), timezone.utc),
        timeline.get("playing_start"),
        winner.get("teamId"),
        winner.get("teamName"),
        winner.get("label_source"),
    )


def squad_tuples(game_id: int, timeline: dict) -> list[tuple]:
    names: dict[Any, str | None] = {}
    cached = timeline.get("squads")
    if isinstance(cached, list) and cached:
        return [
            (game_id, squad.get("teamId"), squad.get("teamName"))
            for squad in cached
            if squad.get("teamId") is not None
        ]
    for frame in timeline.get("frames") or []:
        for player in frame.get("players") or []:
            team_id = player.get("teamId")
            if team_id is None:
                continue
            if team_id not in names or (names[team_id] is None and player.get("teamName")):
                names[team_id] = player.get("teamName")
    return [(game_id, team_id, names[team_id]) for team_id in sorted(names, key=str)]


def player_tuples(record: dict, timeline: dict) -> list[tuple]:
    """One row per player per Playing second. None stays None for NULL."""
    game_id = record["game_id"]
    rows = []
    for frame in timeline.get("frames") or []:
        ts = replay_timestamp(record["match_start"], frame["t"])
        for player in frame.get("players") or []:
            if not player.get("id"):
                continue
            rows.append(
                (
                    ts,
                    game_id,
                    int(frame["t"]),
                    int(frame["elapsed"]),
                    str(player["id"]),
                    player.get("name"),
                    player.get("teamId"),
                    player.get("x"),
                    player.get("y"),
                    player.get("health"),
                    player.get("healthMax"),
                    player.get("shields"),
                    player.get("shieldMax"),
                    player.get("status"),
                )
            )
    return rows


def _estimate_rows(game_id: int, kind: str, version: str | None, points: list[dict]) -> list[tuple]:
    rows = []
    for point in points:
        for squad in point.get("squads") or []:
            team_id = squad.get("teamId")
            if team_id is None:
                continue
            probability = squad.get("probability")
            if probability is None:
                probability = squad.get("model")
            baseline = squad.get("baseline_probability")
            if baseline is None:
                baseline = squad.get("uniform_contention")
            rows.append(
                (
                    game_id,
                    int(point["t"]),
                    int(point["elapsed"]),
                    int(team_id),
                    kind,
                    version,
                    probability,
                    BASELINE,
                    baseline,
                    squad.get("in_contention"),
                )
            )
    return rows


def held_out_rows(data_dir: Path, game_id: int) -> list[tuple]:
    """Read a held-out bundle only. Never copies final-model probabilities."""
    bundled = data_dir / "models" / "held_out" / f"{game_id}.json"
    shared = data_dir / "models" / "held_out.json"
    path = bundled if bundled.is_file() else shared if shared.is_file() else None
    if path is None:
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    version = document.get("model_version")
    version_text = json.dumps(version, sort_keys=True) if isinstance(version, dict) else version
    games = document.get("games") or {}
    points = games.get(str(game_id)) or games.get(game_id) or []
    if isinstance(points, dict):
        points = points.get("points") or []
    return _estimate_rows(game_id, HELD_OUT, version_text, points)


def fitted_rows(data_dir: Path, game_id: int) -> list[tuple]:
    """Forward pass of model.json. Empty when that file is absent."""
    path = data_dir / "models" / "model.json"
    if not path.is_file():
        return []
    model = json.loads(path.read_text(encoding="utf-8"))
    from collect_matches import stats_game_for
    from features import game_examples
    from train_model import contention_baseline, predict

    record = load_record(data_dir, game_id)
    raw = _raw_path(record, data_dir)
    if raw is None:
        return []
    payload = json.loads(raw.read_text(encoding="utf-8-sig"))
    game = game_examples(payload, record, stats_game_for(record, data_dir))
    version = json.dumps(
        {
            "l2": model.get("l2"),
            "checkpoint_step_s": model.get("checkpoint_step_s"),
            "features": model.get("features"),
        },
        sort_keys=True,
    )
    points = []
    for group in game.get("groups") or []:
        probs = predict(model, group)
        baseline = contention_baseline(group)
        squads = []
        for index, row in enumerate(group["rows"]):
            squads.append(
                {
                    "teamId": row["team"],
                    "probability": probs[index],
                    "baseline_probability": baseline[index],
                    "in_contention": baseline[index] > 0,
                }
            )
        points.append({"t": group["t"], "elapsed": group["elapsed"], "squads": squads})
    return _estimate_rows(game_id, FITTED, version, points)


def estimate_rows(data_dir: Path, game_id: int) -> list[tuple]:
    held = held_out_rows(data_dir, game_id)
    fitted = fitted_rows(data_dir, game_id)
    if any(row[4] == HELD_OUT for row in fitted):
        raise RuntimeError("final-model rows were labeled held_out")
    if any(row[4] == FITTED for row in held):
        raise RuntimeError("held-out rows were labeled final_model_fitted")
    return held + fitted


def evaluation_tuple(data_dir: Path) -> tuple | None:
    path = data_dir / "models" / "report.json"
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    evaluation = report.get("evaluation") or {}
    if evaluation.get("status") != "ok":
        return None
    overall = ((evaluation.get("test") or {}).get("overall") or {}).get("game_weighted") or {}
    model_loss = (overall.get("model") or {}).get("log_loss")
    baseline_loss = (overall.get("uniform_contention") or {}).get("log_loss")
    condition = ((report.get("ablation") or {}).get("condition_only") or {})
    condition_loss = (
        (((condition.get("test") or {}).get("overall") or {}).get("game_weighted") or {})
        .get("model", {})
        .get("log_loss")
    )
    games = evaluation.get("games") or {}
    sessions = evaluation.get("sessions") or {}
    test_games = games.get("test")
    test_sessions = len(sessions.get("test") or [])
    return (
        1,
        report.get("generated_at"),
        evaluation.get("method"),
        evaluation.get("method") == "session_split",
        test_sessions,
        test_games,
        model_loss,
        BASELINE,
        baseline_loss,
        condition_loss,
        "Held-out aggregates. Not a decision effect. Positional features are compared via condition_only_log_loss.",
    )


def frame_at(rows: list[tuple], t: int) -> list[tuple]:
    """Rows whose replay clock is the latest t at or before the request."""
    eligible = [row for row in rows if row[2] <= t]
    if not eligible:
        return []
    chosen = max(row[2] for row in eligible)
    return [row for row in eligible if row[2] == chosen]


def player_count(timeline: dict) -> int:
    return sum(len(frame.get("players") or []) for frame in timeline.get("frames") or [])
