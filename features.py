"""Per-team features at Playing checkpoints, built from the existing decoder.

One row is one team at one checkpoint ``t`` of one accepted game. Every
feature is read from ``overstat_patch.export_players`` at that ``t``, so no
row sees anything written after its checkpoint. The label is the only value
taken from the end of the replay: ``collection.winning_squad`` on the final
reconstructed state.

Quality score is selection metadata, not a feature (see ``collection.py``).

Coordinate units are undefined (see docs/historical-state-decoder.md), so
distances are only used relative to the other teams in the same snapshot.
An unknown value stays ``None``. It is never filled with zero here; the
model decides how to impute.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from collection import winning_squad
from overstat_patch import default_checkpoints, replay

STANDING = ("alive", "downed")
OUT = ("eliminated", "dead")

# A position whose last write is older than this many seconds at the
# checkpoint counts as stale for ``stale_pos_frac``. It is a descriptive
# feature, not an acceptance rule.
STALE_GAP = 30

FEATURES = (
    "progress",
    "teams_standing",
    "players_alive",
    "players_downed",
    "players_out",
    "alive_frac",
    "hp_frac",
    "hp_frac_missing",
    "stale_pos_frac",
    "center_dist_rel",
    "center_dist_missing",
    "spread_rel",
    "spread_missing",
)


def team_key(row: dict) -> Any:
    """Team identity in a snapshot row: ``teamId``, else ``teamName``."""
    team_id = row["teamId"]["value"]
    if team_id is not None:
        return team_id
    return row["teamName"]["value"]


def winner_key(winner: dict | None) -> Any:
    if not winner:
        return None
    if winner.get("teamId") is not None:
        return winner["teamId"]
    return winner.get("teamName")


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


def team_rows(snapshot: list[dict], t: int, playing: tuple[int, int]) -> list[dict]:
    """Feature rows for every team that still has a standing or unknown player."""
    teams: dict[Any, list[dict]] = {}
    for row in snapshot:
        key = team_key(row)
        if key is None:
            continue
        teams.setdefault(key, []).append(row)

    start, end = playing
    progress = (t - start) / (end - start) if end > start else 0.0
    candidates = {}
    for key, rows in teams.items():
        statuses = [row["status"]["value"] for row in rows]
        if all(status in OUT for status in statuses):
            continue
        candidates[key] = rows

    centers: dict[Any, tuple[float, float] | None] = {}
    spreads: dict[Any, float | None] = {}
    raw: dict[Any, dict] = {}
    for key, rows in candidates.items():
        standing = [row for row in rows if row["status"]["value"] not in OUT]
        alive = sum(1 for row in rows if row["status"]["value"] == "alive")
        downed = sum(1 for row in rows if row["status"]["value"] == "downed")
        out = sum(1 for row in rows if row["status"]["value"] in OUT)

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
        hp_frac = hp_now / hp_max if hp_max > 0 else None

        points = []
        stale = 0
        for row in standing:
            point = _position(row)
            gaps = [row["pos"]["gap"]["x"], row["pos"]["gap"]["y"]]
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
            "players_alive": alive,
            "players_downed": downed,
            "players_out": out,
            "alive_frac": alive / len(rows),
            "hp_frac": hp_frac,
            "stale_pos_frac": stale / len(standing) if standing else 1.0,
        }

    # Distance from each team to the centroid of every other standing team.
    # Late in a match the survivors close in on the ring, so a team far from
    # the pack is a plausible proxy for being outside it.
    dists: dict[Any, float | None] = {}
    for key, center in centers.items():
        others = [c for other, c in centers.items() if other != key and c is not None]
        field = _centroid(others)
        dists[key] = _distance(center, field) if center is not None and field is not None else None
    dist_rel = _relative(dists)
    spread_rel = _relative(spreads)

    out_rows = []
    for key in sorted(candidates, key=lambda k: (not isinstance(k, int), str(k))):
        values = dict(raw[key])
        values["progress"] = progress
        values["teams_standing"] = len(candidates)
        values["hp_frac_missing"] = 1.0 if values["hp_frac"] is None else 0.0
        values["center_dist_rel"] = dist_rel[key]
        values["center_dist_missing"] = 1.0 if dist_rel[key] is None else 0.0
        values["spread_rel"] = spread_rel[key]
        values["spread_missing"] = 1.0 if spread_rel[key] is None else 0.0
        out_rows.append({"team": key, "features": {name: values[name] for name in FEATURES}})
    return out_rows


def checkpoint_times(state_names: list[Any], count: int) -> list[int]:
    """``count`` evenly spaced Playing seconds, start and end included.

    ``count == 3`` gives the decoder's own ``default_checkpoints``.
    """
    start, _middle, end = default_checkpoints(state_names)
    if count == 3:
        return [start, _middle, end]
    if count < 2:
        raise ValueError("need at least two checkpoints")
    return sorted({start + round(i * (end - start) / (count - 1)) for i in range(count)})


def game_examples(payload: dict, game_id: Any, checkpoints: int = 3) -> dict:
    """Rows for one game, or a reason it produced none.

    Returns ``{"game_id", "winner", "groups", "skipped"}``. A group is one
    checkpoint: ``{"game_id", "t", "rows", "label"}`` where ``label`` is the
    index of the winning team in ``rows``. A checkpoint where the winner is
    not among the standing teams is skipped and recorded, not relabeled.
    """
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list) or not series:
        return {"game_id": game_id, "winner": None, "groups": [], "skipped": [{"reason": "invalid_payload"}]}
    probe = replay(series)
    if probe["failures"]:
        return {"game_id": game_id, "winner": None, "groups": [], "skipped": [{"reason": "reconstruction_failed"}]}
    try:
        times = checkpoint_times(probe["state_names"], checkpoints)
    except ValueError as exc:
        return {"game_id": game_id, "winner": None, "groups": [], "skipped": [{"reason": str(exc)}]}
    winner = winner_key(winning_squad(probe["state"]))
    if winner is None:
        return {"game_id": game_id, "winner": None, "groups": [], "skipped": [{"reason": "outcome_unknown"}]}

    result = replay(series, set(times))
    playing = (times[0], times[-1])
    groups = []
    skipped = []
    for t in times:
        rows = team_rows(result["snapshots"].get(t, []), t, playing)
        keys = [row["team"] for row in rows]
        if winner not in keys:
            skipped.append({"t": t, "reason": "winner_not_standing"})
            continue
        groups.append({"game_id": game_id, "t": t, "rows": rows, "label": keys.index(winner)})
    return {"game_id": game_id, "winner": winner, "groups": groups, "skipped": skipped}


def raw_path(record: dict, data_dir: Path) -> Path | None:
    """The cached timeseries for a manifest record.

    ``download_path`` is written relative to the collector's working
    directory, so the ``data_dir/raw`` layout is tried as well.
    """
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
    """Accepted manifest records with a readable raw file, plus an inventory.

    Only ``acceptance_status == "accepted"`` counts. Records that repeat a
    raw file's sha256 are dropped as duplicates of the first one.
    """
    manifest = data_dir / "collection" / "manifest.json"
    inventory = {
        "manifest": manifest.as_posix(),
        "manifest_exists": manifest.is_file(),
        "games_in_manifest": 0,
        "accepted_in_manifest": 0,
        "accepted_missing_raw": [],
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
        inventory["accepted_in_manifest"] += 1
        path = raw_path(record, data_dir)
        if path is None:
            inventory["accepted_missing_raw"].append(record.get("game_id", key))
            continue
        digest = record.get("sha256")
        if digest and digest in seen_hash:
            inventory["duplicate_payloads"].append(
                {"game_id": record.get("game_id", key), "same_as": seen_hash[digest]}
            )
            continue
        if digest:
            seen_hash[digest] = record.get("game_id", key)
        usable.append({**record, "raw_path": path.as_posix()})
    inventory["usable"] = len(usable)
    inventory["sessions"] = sorted({str(record.get("session_id")) for record in usable})
    return usable, inventory


def build_dataset(records: list[dict], checkpoints: int = 3) -> tuple[list[dict], list[dict]]:
    """Checkpoint groups for every record, and the games that produced none."""
    groups = []
    dropped = []
    for record in records:
        payload = json.loads(Path(record["raw_path"]).read_text(encoding="utf-8-sig"))
        game = game_examples(payload, record.get("game_id"), checkpoints)
        expected = winner_key((record.get("validation") or {}).get("winner"))
        if expected is not None and game["winner"] is not None and expected != game["winner"]:
            dropped.append({"game_id": record.get("game_id"), "reason": "winner_mismatch_with_manifest"})
            continue
        for group in game["groups"]:
            group["session_id"] = record.get("session_id")
        groups.extend(game["groups"])
        if not game["groups"]:
            dropped.append({"game_id": record.get("game_id"), "reasons": game["skipped"]})
    return groups, dropped
