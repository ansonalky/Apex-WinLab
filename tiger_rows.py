"""Turn file-backed match data into Tiger Data rows.

This module does not open a database and does not change the decoder or model.
Unknown player fields stay None so the loader can insert NULL.

Provenance rules:

- ``held_out`` rows come only from the verified export bundle
  ``data/exports/heldout-session-split-v2/``, validated by
  ``dashboard_data.load_heldout_bundle`` (labels and file hashes). There is
  no other source and no fallback.
- ``final_model_fitted`` rows are a forward pass of ``data/models/model.json``.
- Neither kind is ever written under the other's label.
- ``model_probability`` is eligibility-constrained: squads with a verified
  elimination at or before the checkpoint are exactly 0, the rest renormalized.
  ``model_probability_raw`` keeps the unconstrained output.

Time: ``ts`` is the reconstructed replay's own ``match_start`` + ``t``. The
manifest ``match_start`` comes from the games API and is 7-16 s earlier on
every accepted game, so it is stored separately and never used for ``ts``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HELD_OUT = "held_out"
FITTED = "final_model_fitted"
KINDS = (HELD_OUT, FITTED)
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


def load_timeline(data_dir: Path, game_id: int, store=None) -> dict:
    """The dashboard's versioned timeline (one decoder pass, cached on disk)."""
    from dashboard_data import MatchStore

    timeline = (store or MatchStore(data_dir)).timeline(game_id)
    if timeline is None:
        raise SystemExit(f"no raw telemetry for game {game_id}")
    return timeline


def _epoch(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a time")
    return int(value)


def replay_timestamp(replay_match_start: Any, t: int) -> datetime:
    """Absolute time of replay clock ``t``: the replay's ``match_start`` + ``t``."""
    return datetime.fromtimestamp(_epoch(replay_match_start) + int(t), timezone.utc)


def _replay_start(timeline: dict, game_id: Any) -> Any:
    start = timeline.get("replay_match_start")
    if start in (None, ""):
        raise SystemExit(f"game {game_id}: replay match_start is missing; refusing to fall back to the manifest time")
    return start


def match_tuple(record: dict, timeline: dict, split: str | None = None) -> tuple:
    start = record.get("match_start")
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        raise SystemExit(f"game {record.get('game_id')} has no numeric match_start")
    replay_start = _replay_start(timeline, record.get("game_id"))
    winner = (record.get("validation") or {}).get("winner") or {}
    return (
        record.get("game_id"),
        record.get("session_id"),
        record.get("event"),
        record.get("map_name"),
        record.get("quality_score"),
        record.get("organizer_username"),
        datetime.fromtimestamp(int(start), timezone.utc),
        replay_timestamp(replay_start, 0),
        timeline.get("playing_start"),
        split,
        winner.get("teamId"),
        winner.get("teamName"),
        winner.get("label_source"),
    )


def squad_tuples(game_id: int, timeline: dict) -> list[tuple]:
    return [
        (game_id, squad.get("teamId"), squad.get("teamName"))
        for squad in timeline.get("squads") or []
        if squad.get("teamId") is not None
    ]


def player_tuples(record: dict, timeline: dict) -> list[tuple]:
    """One row per player per Playing second. None stays None for NULL."""
    game_id = record["game_id"]
    replay_start = _replay_start(timeline, game_id)
    rows = []
    for frame in timeline.get("frames") or []:
        ts = replay_timestamp(replay_start, frame["t"])
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


def _estimate_rows(game_id: int, kind: str, version: str, replay_start: Any, points: list[dict]) -> list[tuple]:
    """Rows in ``INSERT_ESTIMATE`` column order."""
    rows = []
    for point in points:
        for squad in point["squads"]:
            rows.append(
                (
                    game_id,
                    int(point["t"]),
                    int(point["elapsed"]),
                    replay_timestamp(replay_start, point["t"]),
                    int(squad["teamId"]),
                    kind,
                    version,
                    squad["model"],
                    squad["model_unconstrained"],
                    BASELINE,
                    squad["uniform_contention"],
                    bool(squad["in_contention"]),
                    bool(squad["eliminated"]),
                    bool(point["elimination_verified"]),
                )
            )
    return rows


def _contending(point: dict, squad: dict) -> bool:
    return not squad["eliminated"] if point["elimination_verified"] else True


def held_out_rows(data_dir: Path, game_id: int, timeline: dict, store=None) -> tuple[list[tuple], str]:
    """Held-out rows from the verified bundle, joined like the dashboard does.

    Returns ``(rows, status)``. Rows are empty when the bundle is missing or
    invalid, or when the game is not a test game; ``status`` says which.
    """
    from dashboard_data import MatchStore, heldout_view

    store = store or MatchStore(data_dir)
    bundle = store.bundle
    if not bundle.get("available"):
        return [], f"bundle unavailable: {bundle.get('reason')}"
    view = heldout_view(bundle, game_id, timeline)
    if not view.get("available"):
        split = view.get("split")
        return [], f"not held-out ({split})" if split in ("train", "validation") else f"unavailable: {view.get('reason')}"
    meta = bundle["meta"]
    version = json.dumps(
        {
            "source": f"data/exports/{bundle['name']}",
            "prediction_type": meta.get("prediction_type"),
            "bundle_generated_at": meta.get("generated_at"),
            "code_commit": (meta.get("code") or {}).get("commit"),
            "fit_on": (meta.get("evaluation_model") or {}).get("fit_on"),
            "l2": (meta.get("evaluation_model") or {}).get("l2"),
            "predictions_sha256": ((meta.get("files") or {}).get("heldout_predictions.json") or {}).get("sha256"),
        },
        sort_keys=True,
    )
    points = [
        {**point, "squads": [{**squad, "in_contention": _contending(point, squad)} for squad in point["squads"]]}
        for point in view["points"]
    ]
    return _estimate_rows(game_id, HELD_OUT, version, _replay_start(timeline, game_id), points), "held-out test game"


def fitted_rows(data_dir: Path, game_id: int, timeline: dict, store=None) -> tuple[list[tuple], str]:
    """Final-model forward pass, from the same curve the dashboard shows."""
    from dashboard_data import MatchStore

    store = store or MatchStore(data_dir)
    curve = store.curve(game_id)
    if not curve.get("available"):
        return [], f"unavailable: {curve.get('reason')}"
    model_version = curve.get("model_version") or {}
    version = json.dumps(
        {
            "source": "data/models/model.json",
            "prediction_type": FITTED,
            **{key: model_version.get(key) for key in ("l2", "checkpoint_step_s", "features", "trained_on_game_count")},
            "in_trained_on_games": curve.get("in_sample_game"),
        },
        sort_keys=True,
    )
    points = [
        {**point, "squads": [{**squad, "in_contention": _contending(point, squad)} for squad in point["squads"]]}
        for point in curve["points"]
    ]
    return _estimate_rows(game_id, FITTED, version, _replay_start(timeline, game_id), points), "fitted on all accepted games"


def estimate_rows(data_dir: Path, game_id: int, timeline: dict, store=None) -> dict[str, tuple[list[tuple], str]]:
    """``{kind: (rows, status)}``. A kind with no rows is not replaced on import."""
    out = {
        HELD_OUT: held_out_rows(data_dir, game_id, timeline, store),
        FITTED: fitted_rows(data_dir, game_id, timeline, store),
    }
    for kind, (rows, _status) in out.items():
        if any(row[5] != kind for row in rows):
            raise RuntimeError(f"{kind} rows carry another prediction kind")
    return out


def evaluation_tuple(data_dir: Path, frozen_path: Path = Path("config/frozen_evaluation.json")) -> tuple | None:
    """Held-out aggregate from report.json, cross-checked with the frozen config."""
    path = data_dir / "models" / "report.json"
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    evaluation = report.get("evaluation") or {}
    if evaluation.get("status") != "ok" or evaluation.get("method") != "session_split":
        return None
    overall = (evaluation.get("test") or {}).get("overall") or {}
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        if frozen.get("test_metrics") != overall:
            raise SystemExit("report.json test metrics differ from the frozen evaluation; not importing the report")
    weighted = overall.get("game_weighted") or {}
    condition = (report.get("ablation") or {}).get("condition_only") or {}
    condition_loss = (
        (((condition.get("test") or {}).get("overall") or {}).get("game_weighted") or {}).get("model", {}).get("log_loss")
    )
    return (
        1,
        report.get("generated_at"),
        evaluation.get("method"),
        True,
        len((evaluation.get("sessions") or {}).get("test") or []),
        (evaluation.get("games") or {}).get("test"),
        (weighted.get("model") or {}).get("log_loss"),
        BASELINE,
        (weighted.get(BASELINE) or {}).get("log_loss"),
        condition_loss,
        json.dumps(weighted, sort_keys=True),
        "Held-out session-split test aggregates (frozen). Not a decision effect.",
    )


def frame_at(rows: list[tuple], t: int) -> list[tuple]:
    """Rows whose replay clock is the latest t at or before the request."""
    eligible = [row for row in rows if row[2] <= t]
    if not eligible:
        return []
    chosen = max(row[2] for row in eligible)
    return [row for row in eligible if row[2] == chosen]


def player_count(timeline: dict) -> int:
    return sum(1 for frame in timeline.get("frames") or [] for player in frame.get("players") or [] if player.get("id"))
