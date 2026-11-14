"""Per-squad features at fixed 30-second Playing checkpoints.

One group is one checkpoint ``t`` of one accepted game. Its rows are the
eligible squads at ``t`` and exactly one of them is the verified eventual
winner. Every feature is read from ``overstat_patch.export_players`` at
``t``. Nothing written after ``t`` reaches a row.

Checkpoints are ``playing_start + 30k`` for k = 0, 1, 2, ... and stop at the
first frame where the replay has left ``Playing`` or reports
``teamsAlive <= 1``. Both are visible at that frame, so the cut-off uses no
information from later in the game, and eventual match length never selects
an earlier checkpoint.

Label: the manifest's ``validation.winner``, accepted only when its
``label_source`` includes this game's standings, and only when
``collection.resolve_winner`` on our own replay against the same standings
names the same ``teamId``.

Squad eligibility: every squad (``teamId``) present in the snapshot. A squad
whose players are all ``eliminated`` or ``dead`` is kept and flagged
(``no_standing``), because the meaning of those statuses is not verified:
the collector's survivor rule found no standing squad at the end of three
real games that do have a verified winner. See docs/model-pipeline.md.

Quality score is selection metadata, not a feature. Coordinate units are
undefined, so distances are only compared within one snapshot.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from collect_matches import stats_game_for
from collection import resolve_winner
from overstat_patch import apply_frame, export_players, frame_entries, initial_state

CHECKPOINT_STEP = 30
STANDING = ("alive", "downed")
OUT = ("eliminated", "dead")
KNOWN_STATUS = STANDING + OUT
STANDINGS_SOURCE = "stats.games[id].teams.overall_stats.teamPlacement"

# A position whose last write is older than this at the checkpoint counts as
# stale for ``stale_pos_frac``. Descriptive feature, not an acceptance rule.
STALE_GAP = 30

# Group-constant values (elapsed time, squads remaining) cancel out of a
# conditional logit, so every feature here varies between squads.
FEATURES = (
    "players_alive",
    "players_downed",
    "players_out",
    "players_status_unknown",
    "no_standing",
    "hp_frac",
    "hp_frac_missing",
    "stale_pos_frac",
    "center_dist_rel",
    "center_dist_missing",
    "spread_rel",
    "spread_missing",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _position(row: dict) -> tuple[float, float] | None:
    """Horizontal position. ``z`` is left out because its units are unknown."""
    x = _number(row["pos"]["value"]["x"])
    y = _number(row["pos"]["value"]["y"])
    if x is None or y is None:
        return None
    return x, y


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _relative(values: dict[Any, float | None]) -> dict[Any, float | None]:
    """Divide by the snapshot mean of the known values. Unknown stays unknown."""
    known = [value for value in values.values() if value is not None]
    mean = sum(known) / len(known) if known else 0.0
    if mean <= 0:
        return {key: (0.0 if value is not None else None) for key, value in values.items()}
    return {key: (value / mean if value is not None else None) for key, value in values.items()}


def squad_rows(snapshot: list[dict]) -> list[dict]:
    """One feature row per ``teamId`` in the snapshot. No squad is dropped."""
    squads: dict[Any, list[dict]] = {}
    for row in snapshot:
        team_id = row["teamId"]["value"]
        if team_id is None:
            continue
        squads.setdefault(team_id, []).append(row)

    centers: dict[Any, tuple[float, float] | None] = {}
    spreads: dict[Any, float | None] = {}
    raw: dict[Any, dict] = {}
    for key, rows in squads.items():
        statuses = [row["status"]["value"] for row in rows]
        standing = [row for row, status in zip(rows, statuses) if status in STANDING]

        hp_now = 0.0
        hp_max = 0.0
        for row in standing:
            parts = (
                _number(row["health"]["current"]["value"]),
                _number(row["health"]["max"]["value"]),
                _number(row["shields"]["current"]["value"]),
                _number(row["shields"]["max"]["value"]),
            )
            if None in parts:
                continue
            hp_now += parts[0] + parts[2]
            hp_max += parts[1] + parts[3]

        points = []
        stale = 0
        for row in standing:
            point = _position(row)
            gaps = (row["pos"]["gap"]["x"], row["pos"]["gap"]["y"])
            if point is None or any(gap is None or gap > STALE_GAP for gap in gaps):
                stale += 1
            if point is not None:
                points.append(point)
        center = _centroid(points)
        centers[key] = center
        spreads[key] = (
            sum(_distance(point, center) for point in points) / len(points)
            if center is not None
            else None
        )
        raw[key] = {
            "players_alive": float(statuses.count("alive")),
            "players_downed": float(statuses.count("downed")),
            "players_out": float(sum(1 for status in statuses if status in OUT)),
            "players_status_unknown": float(sum(1 for status in statuses if status not in KNOWN_STATUS)),
            "no_standing": 0.0 if standing else 1.0,
            "hp_frac": hp_now / hp_max if hp_max > 0 else None,
            "stale_pos_frac": stale / len(standing) if standing else None,
        }

    # Distance from each squad to the centroid of the other squads' standing
    # players. Late in a match survivors converge on the ring, so a squad far
    # from the pack may be outside it.
    dists: dict[Any, float | None] = {}
    for key, center in centers.items():
        others = [c for other, c in centers.items() if other != key and c is not None]
        field = _centroid(others)
        dists[key] = _distance(center, field) if center is not None and field is not None else None
    dist_rel = _relative(dists)
    spread_rel = _relative(spreads)

    out_rows = []
    for key in sorted(squads, key=str):
        values = dict(raw[key])
        values["hp_frac_missing"] = 1.0 if values["hp_frac"] is None else 0.0
        values["center_dist_rel"] = dist_rel[key]
        values["center_dist_missing"] = 1.0 if dist_rel[key] is None else 0.0
        values["spread_rel"] = spread_rel[key]
        values["spread_missing"] = 1.0 if spread_rel[key] is None else 0.0
        out_rows.append({"team": key, "features": {name: values[name] for name in FEATURES}})
    return out_rows


def _decided(state: dict) -> bool:
    if state.get("state") != "Playing":
        return True
    alive = state.get("teamsAlive")
    return isinstance(alive, (int, float)) and not isinstance(alive, bool) and alive <= 1


def replay_checkpoints(series: list[dict], step: int = CHECKPOINT_STEP) -> dict:
    """One causal pass with the decoder's ``apply_frame`` and ``export_players``.

    Snapshots are taken at ``start + step*k`` while the game is undecided.
    The pass continues to the end only to obtain the final state for label
    verification; nothing after the cut-off becomes a snapshot.
    """
    state = initial_state()
    observed: dict = {}
    failures = []
    start = None
    cutoff = None
    snapshots: dict[int, list[dict]] = {}
    meta: dict[int, dict] = {}
    for frame in frame_entries(series):
        t = frame["t"]
        events = frame.get("f") or []
        if not isinstance(events, list):
            events = []
        state, observed, error = apply_frame(state, frame.get("d"), events, observed, t)
        if error:
            failures.append({"t": t, "error": error})
        if start is None:
            if state.get("state") != "Playing":
                continue
            start = t
        if cutoff is not None:
            continue
        if _decided(state):
            cutoff = t
            continue
        if (t - start) % step == 0:
            snapshots[t] = export_players(state, observed, t)
            meta[t] = {"teamsAlive": state.get("teamsAlive"), "elapsed": t - start}
    return {
        "state": state,
        "failures": failures,
        "playing_start": start,
        "cutoff": cutoff,
        "snapshots": snapshots,
        "meta": meta,
    }


def verified_label(record: dict) -> dict | None:
    """The manifest winner, only when it was checked against this game's standings."""
    winner = (record.get("validation") or {}).get("winner")
    if not isinstance(winner, dict) or winner.get("teamId") is None:
        return None
    if STANDINGS_SOURCE not in (winner.get("label_source") or ""):
        return None
    return winner


def game_examples(payload: dict, record: dict, stats_game: dict | None, step: int = CHECKPOINT_STEP) -> dict:
    """Groups for one game, or why it produced none.

    Returns ``{"game_id", "winner", "groups", "skipped", "diagnostics"}``.
    """
    game_id = record.get("game_id")
    result = {"game_id": game_id, "winner": None, "groups": [], "skipped": [], "diagnostics": {}}
    label = verified_label(record)
    if label is None:
        result["skipped"].append({"reason": "label_not_verified_against_standings"})
        return result
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list) or not series:
        result["skipped"].append({"reason": "invalid_payload"})
        return result
    replayed = replay_checkpoints(series, step)
    if replayed["failures"]:
        result["skipped"].append({"reason": "reconstruction_failed"})
        return result
    if replayed["playing_start"] is None:
        result["skipped"].append({"reason": "never_playing"})
        return result
    if stats_game is None:
        # Without standings ``resolve_winner`` falls back to the replay-only
        # survivor rule, which is not the verification the label requires.
        result["skipped"].append({"reason": "standings_missing_for_cross_check"})
        return result
    check = resolve_winner(replayed["state"], stats_game)
    if check is None or check.get("teamId") != label["teamId"]:
        result["skipped"].append(
            {"reason": "winner_cross_check_failed", "replay": (check or {}).get("teamId"), "manifest": label["teamId"]}
        )
        return result

    winner = label["teamId"]
    result["winner"] = winner
    diagnostics = {
        "playing_start": replayed["playing_start"],
        "cutoff": replayed["cutoff"],
        "checkpoints": len(replayed["snapshots"]),
        "winner_no_standing": [],
        "squads_no_standing": 0,
        "squad_rows": 0,
    }
    for t in sorted(replayed["snapshots"]):
        rows = squad_rows(replayed["snapshots"][t])
        keys = [row["team"] for row in rows]
        if winner not in keys:
            result["skipped"].append({"t": t, "reason": "winner_not_in_snapshot"})
            continue
        label_index = keys.index(winner)
        if rows[label_index]["features"]["no_standing"]:
            diagnostics["winner_no_standing"].append(t)
        diagnostics["squads_no_standing"] += sum(int(row["features"]["no_standing"]) for row in rows)
        diagnostics["squad_rows"] += len(rows)
        result["groups"].append(
            {
                "game_id": game_id,
                "session_id": record.get("session_id"),
                "t": t,
                "elapsed": replayed["meta"][t]["elapsed"],
                "rows": rows,
                "label": label_index,
            }
        )
    result["diagnostics"] = diagnostics
    return result


def raw_path(record: dict, data_dir: Path) -> Path | None:
    """The cached timeseries. ``download_path`` is relative to the collector's cwd."""
    candidates = []
    if record.get("download_path"):
        candidates.append(Path(record["download_path"]))
    if record.get("livedata_id"):
        candidates.append(data_dir / "raw" / f"{record['livedata_id']}.timeseries.json")
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_accepted(data_dir: Path) -> tuple[list[dict], dict]:
    """Accepted manifest records with a raw file and a verified label, plus an inventory.

    Records that repeat a raw file's sha256 are dropped as duplicates.
    """
    manifest = data_dir / "collection" / "manifest.json"
    inventory = {
        "manifest": manifest.as_posix(),
        "manifest_exists": manifest.is_file(),
        "games_in_manifest": 0,
        "accepted_in_manifest": 0,
        "accepted_missing_raw": [],
        "accepted_label_unverified": [],
        "accepted_missing_sha256": [],
        "duplicate_payloads": [],
        "usable": 0,
        "sessions": [],
    }
    if not manifest.is_file():
        return [], inventory
    document = json.loads(manifest.read_text(encoding="utf-8"))
    games = document.get("games") or {}
    inventory["games_in_manifest"] = len(games)
    usable = []
    seen_hash: dict[str, Any] = {}
    for key, record in games.items():
        if not isinstance(record, dict) or record.get("acceptance_status") != "accepted":
            continue
        game_id = record.get("game_id", key)
        inventory["accepted_in_manifest"] += 1
        if verified_label(record) is None:
            inventory["accepted_label_unverified"].append(game_id)
            continue
        path = raw_path(record, data_dir)
        if path is None:
            inventory["accepted_missing_raw"].append(game_id)
            continue
        digest = record.get("sha256")
        if not digest:
            inventory["accepted_missing_sha256"].append(game_id)
        elif digest in seen_hash:
            inventory["duplicate_payloads"].append({"game_id": game_id, "same_as": seen_hash[digest]})
            continue
        else:
            seen_hash[digest] = game_id
        usable.append({**record, "raw_path": path.as_posix()})
    inventory["usable"] = len(usable)
    inventory["sessions"] = sorted({str(record.get("session_id")) for record in usable})
    return usable, inventory


def build_dataset(records: list[dict], data_dir: Path, step: int = CHECKPOINT_STEP) -> tuple[list[dict], list[dict], dict]:
    """Groups for every record, games that produced none, and eligibility diagnostics."""
    groups = []
    dropped = []
    diagnostics = {}
    for record in records:
        payload = json.loads(Path(record["raw_path"]).read_text(encoding="utf-8-sig"))
        game = game_examples(payload, record, stats_game_for(record, data_dir), step)
        groups.extend(game["groups"])
        if game["diagnostics"]:
            diagnostics[str(record.get("game_id"))] = game["diagnostics"]
        if not game["groups"] or game["skipped"]:
            dropped.append({"game_id": record.get("game_id"), "groups": len(game["groups"]), "skipped": game["skipped"]})
    return groups, dropped, diagnostics
