"""Read collector telemetry for the dashboard. Does not train or alter the model.

Two kinds of prediction are shown, and they are never mixed:

- **Held-out** (primary): per-checkpoint predictions from the export bundle
  ``data/exports/<bundle>/``. They exist only for the frozen split's test
  games and were made by a model fit on other sessions.
- **Final model** (secondary): a forward pass of ``data/models/model.json``,
  which was fit on every accepted game, including the one on screen.

A missing or invalid artifact is shown as "Not available". Between 30-second
checkpoints the dashboard shows the most recent past checkpoint, labeled with
its own time. It never selects a future checkpoint and never interpolates.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from features import CHECKPOINT_STEP, contention, elimination_team, squad_rows
from overstat_patch import apply_frame, export_players, frame_entries, initial_state

HELDOUT_BUNDLE = "heldout-session-split-v2"
HELDOUT_PREDICTION_TYPE = "held_out_session_split_test"
HELDOUT_SCHEMA_VERSION = 2
# Bump when the cached timeline layout changes; older caches are rebuilt.
CACHE_VERSION = 3

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


def numeric_quality(value: Any) -> Decimal | None:
    """The stored quality number. Display text and non-numbers are not used."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite():
        return None
    return number


def _game_id_number(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def sort_matches(rows: list[dict]) -> list[dict]:
    """Highest stored quality first. Missing quality last. Ties by game id descending."""

    def key(item: dict) -> tuple:
        quality = numeric_quality(item.get("quality_score"))
        game_id = _game_id_number(item.get("game_id"))
        return (
            quality is None,
            -(quality if quality is not None else Decimal(0)),
            game_id is None,
            -(game_id if game_id is not None else 0),
        )

    return sorted(rows, key=key)


def list_matches(data_dir: Path, split_of: dict | None = None) -> list[dict]:
    """Accepted games, highest numeric quality first.

    Missing quality scores sort last. Ties break by game id descending.
    ``split_of`` maps game id to its frozen split (train, validation, test);
    ``heldout`` is true only for test games with a valid bundle.
    """
    split_of = split_of or {}
    manifest = data_dir / "collection" / "manifest.json"
    if not manifest.is_file():
        return []
    collection = collection_name(data_dir)
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
                "split": split_of.get(record.get("game_id")),
                "collection": collection,
                "heldout": split_of.get(record.get("game_id")) == "test",
            }
        )
    return sort_matches(rows)


# ---------------------------------------------------------------- expansion collections
# Accepted games outside the frozen 51-game dataset, each collection in its own
# directory with its own manifest (for example ~/Apex-WinLab-expansion/data).
# They are read-only here: never part of the frozen splits, the held-out bundle
# or any model fit, and no prediction is shown for them.
ORIGINAL = "original"
EXPANSION_REASON = (
    "Expansion game: no win estimate is shown. The frozen models were fitted and evaluated on the "
    "original 51 games only; this game is not in the frozen held-out split and has not been evaluated."
)
_EXPANSION_DIRS: list[Path] = []


def register_expansion_dir(path: Path) -> None:
    path = Path(path).expanduser().resolve()
    if not (path / "collection" / "manifest.json").is_file():
        raise SystemExit(f"--expansion-dir has no collection/manifest.json: {path}")
    if path not in _EXPANSION_DIRS:
        _EXPANSION_DIRS.append(path)


def clear_expansion_dirs() -> None:
    _EXPANSION_DIRS.clear()


def expansion_dirs() -> list[Path]:
    return list(_EXPANSION_DIRS)


def collection_name(data_dir: Path) -> str:
    """The manifest's ``package`` name; the production manifest has none and is ``original``."""
    document = _manifest(Path(data_dir) / "collection" / "manifest.json")
    if not isinstance(document, dict):
        return ORIGINAL
    return document.get("package") or ORIGINAL


def is_expansion(record: dict | None) -> bool:
    return bool(record and record.get("_collection"))


def frozen_model_applies(data_dir: Path, record: dict | None) -> bool:
    """Frozen splits and models apply only to the original dataset.

    A registered expansion record, or any game read from a manifest that names
    a package, is outside that dataset.
    """
    if is_expansion(record):
        return False
    directory = Path(record["_data_dir"]) if record and record.get("_data_dir") else Path(data_dir)
    return collection_name(directory) == ORIGINAL


def _record(data_dir: Path, game_id: int) -> dict | None:
    """The accepted record from ``data_dir``, else from a registered expansion
    directory, tagged with ``_data_dir`` and ``_collection``."""
    found = _record_in(data_dir, game_id)
    if found is not None:
        return found
    for extra in _EXPANSION_DIRS:
        if extra == Path(data_dir).resolve():
            continue
        found = _record_in(extra, game_id)
        if found is not None:
            return found | {"_data_dir": str(extra), "_collection": collection_name(extra)}
    from preview_store import preview_record

    return preview_record(game_id)


_MANIFESTS: dict[Path, tuple[int, dict]] = {}


def _manifest(path: Path) -> dict | None:
    """Parsed manifest, reused until the file changes (per-second requests look records up)."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _MANIFESTS.get(path)
    if cached is None or cached[0] != stamp:
        cached = (stamp, json.loads(path.read_text(encoding="utf-8")))
        _MANIFESTS[path] = cached
    return cached[1]


def _record_in(data_dir: Path, game_id: int) -> dict | None:
    document = _manifest(data_dir / "collection" / "manifest.json")
    if document is None:
        return None
    for record in (document.get("games") or {}).values():
        if isinstance(record, dict) and record.get("game_id") == game_id and record.get("acceptance_status") == "accepted":
            return dict(record)
    return None


def _raw_path(record: dict, data_dir: Path) -> Path | None:
    if record.get("_data_dir"):
        # An expansion record: only its own directory, never production data/raw.
        data_dir = Path(record["_data_dir"])
        name = Path(record.get("download_path") or f"{record.get('livedata_id')}.timeseries.json").name
        candidate = data_dir / "raw" / name
        return candidate if candidate.is_file() else None
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


def _utc(start: Any, t: int) -> str | None:
    try:
        unix = int(start) + t
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(unix, timezone.utc).isoformat().replace("+00:00", "Z")


def _decided(state: dict) -> bool:
    alive = state.get("teamsAlive")
    return isinstance(alive, int) and not isinstance(alive, bool) and alive <= 1


def build_timeline(series: list[dict]) -> dict:
    """One decoder pass. Frames run from Playing start to the end of the
    telemetry, one per replay second, so the viewer can reach the recorded
    ``WinnerDetermined`` state. Frames from that state on are ``finished``.

    Checkpoints are every 30 s during Playing only and stop once the replay
    reports ``teamsAlive <= 1``, the same cut-off as ``features``. Frames after
    the outcome are for viewing and never become checkpoints. Squad
    elimination events are tracked causally for the contention baseline.
    """
    state = initial_state()
    observed: dict = {}
    start = None
    decided = False
    concluded = None
    left_playing = False
    frames = []
    checkpoints = []
    eliminated: dict[Any, int] = {}
    problems = 0
    for frame in frame_entries(series):
        t = frame["t"]
        events = frame.get("f") or []
        if not isinstance(events, list):
            events = []
        state, observed, error = apply_frame(state, frame.get("d"), events, observed, t)
        for event in events:
            team, problem = elimination_team(event)
            if problem or (team is not None and team in eliminated):
                problems += 1
            elif team is not None:
                eliminated[team] = t
        if error:
            continue
        playing = state.get("state") == "Playing"
        if start is None:
            if not playing:
                continue
            start = t
        if concluded is None and state.get("state") == "WinnerDetermined":
            concluded = t
        exported = export_players(state, observed, t)
        elapsed = t - start
        frames.append(
            {
                "t": t,
                "elapsed": elapsed,
                "finished": concluded is not None,
                "players": [_compact_player(row) for row in exported],
            }
        )
        left_playing = left_playing or not playing
        if left_playing:
            continue
        decided = decided or _decided(state)
        if elapsed % CHECKPOINT_STEP == 0 and not decided:
            checkpoints.append(
                {
                    "t": t,
                    "elapsed": elapsed,
                    "rows": squad_rows(exported),
                    "meta": {
                        "teamsAlive": state.get("teamsAlive"),
                        "totalTeams": state.get("totalTeams"),
                        "eliminated": sorted(eliminated, key=str),
                        "elimination_problems": problems,
                    },
                }
            )
    return {
        "playing_start": start,
        "replay_match_start": state.get("match_start"),
        "frames": frames,
        "checkpoints": checkpoints,
        "conclusion_t": concluded,
    }


def replay_range(times: list[dict], playing_start: int | None, conclusion_t: int | None) -> dict:
    """Slider bounds in replay seconds, and whether the conclusion is recorded."""
    return {
        "first_t": times[0]["t"] if times else None,
        "last_t": times[-1]["t"] if times else None,
        "conclusion_t": conclusion_t,
        "conclusion_elapsed": None if conclusion_t is None or playing_start is None else conclusion_t - playing_start,
    }


def checkpoint_at(points: list[dict], elapsed: float) -> dict | None:
    """The most recent checkpoint at or before ``elapsed``. Never a later one."""
    times = [point["elapsed"] for point in points]
    index = bisect.bisect_right(times, elapsed) - 1
    if index < 0:
        return None
    point = points[index]
    return {"index": index, "t": point["t"], "elapsed": point["elapsed"], "age_s": elapsed - point["elapsed"]}


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


def _unavailable(reason: str, **extra) -> dict:
    return {"available": False, "message": "Not available", "reason": reason, "points": [], **extra}


def prediction_curve(
    checkpoints: list[dict],
    data_dir: Path,
    game_id: Any,
    winner: Any = None,
    replay_match_start: Any = None,
) -> dict:
    """Final-model forward pass. Fitted on every accepted game; not held-out evidence."""
    path = _model_file(data_dir)
    if not path.is_file() or not checkpoints:
        return _unavailable(
            "data/models/model.json is not in the data directory." if not path.is_file() else "No checkpoints.",
            prediction_type=None,
            limitation="The dashboard does not fit a model.",
        )
    model = json.loads(path.read_text(encoding="utf-8"))
    from train_model import contention_baseline, eligibility, masked, players_baseline, predict, uniform_baseline

    in_sample = game_id in set(model.get("trained_on_games") or [])
    points = []
    for checkpoint in checkpoints:
        rows = [dict(row) for row in checkpoint["rows"]]
        keys = [row["team"] for row in rows]
        status = contention(checkpoint["meta"], keys, winner)
        for row, flag in zip(rows, status["flags"]):
            row["eliminated"] = flag
        group = {"rows": rows, "elimination_verified": status["verified"]}
        raw = predict(model, group)
        # Eligibility rule at this checkpoint: verified-eliminated squads get
        # exactly 0 and the rest are renormalized (unchanged when unverified).
        model_probs = masked(raw, group)
        contention_probs = contention_baseline(group)
        uniform = uniform_baseline(group)
        most = players_baseline(group)
        points.append(
            {
                "t": checkpoint["t"],
                "elapsed": checkpoint["elapsed"],
                "replay_time_utc": _utc(replay_match_start, checkpoint["t"]),
                "elimination_verified": status["verified"],
                "eligibility": eligibility(group),
                "eligibility_reasons": status["reasons"],
                "baseline": "uniform_contention" if status["verified"] else "uniform_lobby_fallback",
                "in_contention": sum(1 for flag in status["flags"] if not flag) if status["verified"] else len(rows),
                "squads": [
                    {
                        "teamId": row["team"],
                        "team_name": row.get("team_name"),
                        "eliminated": row["eliminated"],
                        "model": model_probs[index],
                        "model_unconstrained": raw[index],
                        "uniform_contention": contention_probs[index],
                        "uniform": uniform[index],
                        "most_players": most[index],
                    }
                    for index, row in enumerate(rows)
                ],
            }
        )
    return {
        "available": True,
        "message": None,
        "prediction_type": "final_model_fitted",
        "held_out": False,
        "in_sample_game": in_sample,
        "model_version": {
            "l2": model.get("l2"),
            "checkpoint_step_s": model.get("checkpoint_step_s"),
            "features": model.get("features"),
            "converged": (model.get("optimizer") or {}).get("converged"),
            "trained_on_game_count": len(model.get("trained_on_games") or []),
        },
        "baseline": "uniform_contention: 1 / squads without a verified elimination event; lobby uniform only where elimination is unverified",
        "points": points,
        "limitation": (
            "Fitted on these games: the final model was trained on every accepted game"
            + (", including this one" if in_sample else "")
            + ". This is not held-out evidence. A rise or fall is not a causal effect of a decision."
        ),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_heldout_bundle(data_dir: Path, name: str = HELDOUT_BUNDLE) -> dict:
    """Validated held-out bundle, or why it cannot be used."""
    root = data_dir / "exports" / name
    meta_path = root / "bundle.json"
    if not meta_path.is_file():
        return {"available": False, "reason": f"{meta_path.as_posix()} is not in the data directory."}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"bundle.json unreadable: {exc}"}
    problems = []
    if meta.get("schema_version") != HELDOUT_SCHEMA_VERSION:
        problems.append(f"schema_version {meta.get('schema_version')!r}")
    if meta.get("prediction_type") != HELDOUT_PREDICTION_TYPE:
        problems.append(f"prediction_type {meta.get('prediction_type')!r}")
    if meta.get("held_out") is not True or meta.get("final_model_predictions_included") is not False:
        problems.append("not marked held-out only")
    for file_name, info in (meta.get("files") or {}).items():
        path = root / file_name
        if not path.is_file():
            problems.append(f"{file_name} missing")
        elif _sha256(path) != (info or {}).get("sha256"):
            problems.append(f"{file_name} sha256 mismatch")
    if "heldout_predictions.json" not in (meta.get("files") or {}):
        problems.append("heldout_predictions.json not listed")
    if problems:
        return {"available": False, "reason": "bundle rejected: " + "; ".join(problems)}
    predictions = json.loads((root / "heldout_predictions.json").read_text(encoding="utf-8"))
    metrics_path = root / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
    by_game: dict[Any, list[dict]] = {}
    for entry in predictions:
        by_game.setdefault(entry["game_id"], []).append(entry)
    for entries in by_game.values():
        entries.sort(key=lambda entry: entry["t"])
    config = meta.get("frozen_config") or {}
    split_of = {game: split for split, games in (config.get("games") or {}).items() for game in games}
    return {
        "available": True,
        "name": name,
        "meta": meta,
        "by_game": by_game,
        "per_game_metrics": metrics.get("per_game") or {},
        "split_of": split_of,
    }


def heldout_view(bundle: dict, game_id: Any, timeline: dict) -> dict:
    """Held-out predictions for one game, joined to its timeline on t and teamId."""
    if not bundle.get("available"):
        return _unavailable(bundle.get("reason") or "No held-out bundle.")
    split = bundle["split_of"].get(game_id)
    entries = bundle["by_game"].get(game_id)
    if split != "test" or not entries:
        where = f"the {split} sessions" if split else "no frozen split"
        return _unavailable(
            f"Held-out predictions are not available for this game: it is in {where}, which were used to fit "
            f"or select the evaluation model. Held-out predictions exist only for the "
            f"{len(bundle['by_game'])} test games.",
            split=split,
        )
    own = {checkpoint["t"]: checkpoint for checkpoint in timeline["checkpoints"]}
    problems = []
    points = []
    for entry in entries:
        checkpoint = own.get(entry["t"])
        if checkpoint is None:
            problems.append(f"t {entry['t']} is not a dashboard checkpoint")
            continue
        if checkpoint["elapsed"] != entry["elapsed_s"]:
            problems.append(f"t {entry['t']} elapsed {entry['elapsed_s']} vs {checkpoint['elapsed']}")
        teams = {row["team"] for row in checkpoint["rows"]}
        if teams != {squad["team_id"] for squad in entry["squads"]}:
            problems.append(f"t {entry['t']} squad ids differ")
        points.append(
            {
                "t": entry["t"],
                "elapsed": entry["elapsed_s"],
                "replay_time_utc": entry.get("replay_time_utc"),
                "teams_alive": entry.get("teams_alive"),
                "in_contention": entry.get("in_contention"),
                "elimination_verified": entry.get("elimination_verified"),
                "eligibility": entry.get("eligibility"),
                "eligibility_reasons": entry.get("eligibility_reasons") or [],
                "squads": [
                    {
                        "teamId": squad["team_id"],
                        "team_name": squad.get("team_name"),
                        "eliminated": squad.get("eliminated"),
                        "model": squad["probabilities"]["model_constrained"],
                        "model_unconstrained": squad["probabilities"]["model"],
                        "uniform_contention": squad["probabilities"]["uniform_contention"],
                        "uniform": squad["probabilities"]["uniform"],
                        "most_players": squad["probabilities"]["most_players"],
                    }
                    for squad in entry["squads"]
                ],
            }
        )
    if problems:
        return _unavailable("Held-out join failed: " + "; ".join(problems[:3]), split=split)
    meta = bundle["meta"]
    model = meta.get("evaluation_model") or {}
    return {
        "available": True,
        "message": None,
        "prediction_type": meta.get("prediction_type"),
        "held_out": True,
        "split": split,
        "bundle": bundle["name"],
        "bundle_generated_at": meta.get("generated_at"),
        "code_commit": (meta.get("code") or {}).get("commit"),
        "evaluation_model": {
            "fit_on": model.get("fit_on"),
            "l2": model.get("l2"),
            "converged": (model.get("optimizer") or {}).get("converged"),
            "train_sessions": len((meta.get("frozen_config") or {}).get("sessions", {}).get("train") or []),
            "validation_sessions": len((meta.get("frozen_config") or {}).get("sessions", {}).get("validation") or []),
        },
        "game_metrics": (bundle["per_game_metrics"].get(str(game_id)) or {}).get("game_weighted"),
        "baseline": "uniform_contention",
        "points": points,
        "limitation": (
            "Held-out: made by a model fit on other tournament sessions, with squads verified eliminated by the "
            "prediction time set to 0 and the rest renormalized. Predictions exist every 30 s; "
            "between checkpoints the most recent past prediction is shown with its own time. "
            "Six test games is a small sample; this is demo evidence, not a settled result."
        ),
    }


def constrained_metrics(metrics: dict) -> dict:
    """Name the frozen ``model_masked_diagnostic`` row as the constrained model.

    It is the same eligibility rule (``train_model.masked``) applied to the
    same frozen predictions; the v2 export checks the numbers are equal.
    """
    metrics = dict(metrics)
    if "model_masked_diagnostic" in metrics:
        metrics["model_constrained"] = metrics.pop("model_masked_diagnostic")
    return metrics


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
        "metrics": constrained_metrics(overall),
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

    backend = "files"

    def __init__(self, data_dir: Path, bundle_name: str = HELDOUT_BUNDLE):
        self.data_dir = data_dir
        self._memory: dict[Any, dict] = {}
        self._curves: dict[Any, dict] = {}
        self._views: dict[Any, dict] = {}
        self.bundle = load_heldout_bundle(data_dir, bundle_name)

    def _cache_path(self, game_id: Any) -> Path:
        # An expansion game's timeline cache stays in its own directory.
        record = _record(self.data_dir, game_id)
        root = Path(record["_data_dir"]) if record and record.get("_data_dir") else self.data_dir
        return root / "dashboard" / f"{game_id}.json"

    def matches(self) -> list[dict]:
        # A packaged expansion directory is not the frozen split, even if a bundle is present.
        own_split = None
        if collection_name(self.data_dir) == ORIGINAL and self.bundle.get("available"):
            own_split = self.bundle.get("split_of")
        rows = list_matches(self.data_dir, own_split)
        known = {row["game_id"] for row in rows}
        for extra in _EXPANSION_DIRS:
            if extra == Path(self.data_dir).resolve():
                continue
            # Expansion games: never held-out and never in a frozen split.
            rows += [row | {"heldout": False, "split": None} for row in list_matches(extra) if row["game_id"] not in known]
        return sort_matches(rows)

    def timeline(self, game_id: int) -> dict | None:
        if game_id in self._memory:
            return self._memory[game_id]
        disk = self._cache_path(game_id)
        if disk.is_file():
            loaded = json.loads(disk.read_text(encoding="utf-8"))
            if loaded.get("cache_version") == CACHE_VERSION:
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
        stored = {
            "cache_version": CACHE_VERSION,
            "game_id": game_id,
            "playing_start": built["playing_start"],
            "replay_match_start": built["replay_match_start"],
            "coordinate_note": COORDINATE_NOTE,
            "squads": _squads(built["frames"]),
            "frames": built["frames"],
            "checkpoints": built["checkpoints"],
            "conclusion_t": built["conclusion_t"],
        }
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text(json.dumps(stored) + "\n", encoding="utf-8")
        self._memory[game_id] = stored
        return stored

    def curve(self, game_id: int) -> dict:
        """Final-model curve, computed from the current model.json (not cached on disk)."""
        if game_id not in self._curves:
            timeline = self.timeline(game_id)
            if timeline is None:
                return _unavailable("Match not found.")
            record = _record(self.data_dir, game_id)
            if not frozen_model_applies(self.data_dir, record):
                collection = (record or {}).get("_collection") or collection_name(self.data_dir)
                self._curves[game_id] = _unavailable(EXPANSION_REASON, collection=collection)
                return self._curves[game_id]
            winner = (((record or {}).get("validation") or {}).get("winner") or {}).get("teamId")
            self._curves[game_id] = prediction_curve(
                timeline["checkpoints"], self.data_dir, game_id, winner, timeline.get("replay_match_start")
            )
        return self._curves[game_id]

    def heldout(self, game_id: int) -> dict:
        if game_id not in self._views:
            timeline = self.timeline(game_id)
            if timeline is None:
                return _unavailable("Match not found.")
            record = _record(self.data_dir, game_id)
            if not frozen_model_applies(self.data_dir, record):
                collection = (record or {}).get("_collection") or collection_name(self.data_dir)
                self._views[game_id] = _unavailable(EXPANSION_REASON, collection=collection)
            else:
                self._views[game_id] = heldout_view(self.bundle, game_id, timeline)
        return self._views[game_id]

    @staticmethod
    def _header(view: dict) -> dict:
        return {key: value for key, value in view.items() if key != "points"} | {
            "point_count": len(view.get("points") or [])
        }

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
            "collection": record.get("_collection") or collection_name(self.data_dir),
            # A temporary preview says why it was not saved: the collector's reason,
            # and the replay check's own detail when that check failed.
            **({"storage": "preview", "acceptance_reason": record.get("reason"),
                "acceptance_detail": (record.get("validation") or {}).get("detail")
                if isinstance(record.get("validation"), dict) else None} if record.get("_preview") else {}),
            "winner": {
                "teamId": winner.get("teamId"),
                "teamName": winner.get("teamName"),
                "label_source": winner.get("label_source"),
            },
            "playing_start": timeline["playing_start"],
            "frame_count": len(timeline["frames"]),
            "times": [{"t": frame["t"], "elapsed": frame["elapsed"]} for frame in timeline["frames"]],
            **replay_range(timeline["frames"], timeline["playing_start"], timeline.get("conclusion_t")),
            "squads": timeline["squads"],
            "coordinate_note": timeline["coordinate_note"],
            "heldout": self._header(self.heldout(game_id)),
            "curve": self._header(self.curve(game_id)),
        }

    def frame(self, game_id: int, index: int) -> dict | None:
        """One replay second, plus the past checkpoint each prediction view uses."""
        timeline = self.timeline(game_id)
        if timeline is None or not timeline["frames"]:
            return None
        index = max(0, min(index, len(timeline["frames"]) - 1))
        frame = timeline["frames"][index]
        selected = {}
        for name, view in (("heldout", self.heldout(game_id)), ("final", self.curve(game_id))):
            selected[name] = checkpoint_at(view.get("points") or [], frame["elapsed"]) if view.get("available") else None
        return {"index": index, "squads": timeline["squads"], "checkpoints": selected, **frame}

    def frame_at(self, game_id: int, t: int) -> dict | None:
        """The frame at replay second ``t``, or the latest one before it."""
        timeline = self.timeline(game_id)
        if timeline is None or not timeline["frames"]:
            return None
        index = bisect.bisect_right([frame["t"] for frame in timeline["frames"]], t) - 1
        return self.frame(game_id, max(index, 0)) | {
            "requested_t": t,
            "read": {"source": "files", "via": "timeline cache", "detail": "decoded local replay"},
        }

    def curve_points(self, game_id: int) -> dict:
        return self.curve(game_id)

    def heldout_points(self, game_id: int) -> dict:
        return self.heldout(game_id)

    def evaluation(self) -> dict:
        return evaluation_panel(self.data_dir)
