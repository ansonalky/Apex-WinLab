"""Experimental tactical features. Not used by the frozen model.

Measurements come from ``tactical.measure`` (elevation, observed fire,
opponent proximity and sectors, revealed ring geometry, stale positions).
Combat health is recomputed here so a standing player's health and shields
stay separate from a downed player's bleed-out pool. ``features.squad_rows``
is unchanged.

The model that consumes these columns is an additive conditional logit, the
same ``train_model.fit`` used for the frozen evaluation. A weight is free to
be negative. A column that is identical for every squad at one timestamp
cancels out of that logit. There is no product of elevation and pressure, so
the fit cannot say that height matters only when enemies are close.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from features import replay_checkpoints
from tactical import TacticalIndex, measure

CORRECTED_CONDITION = (
    "players_alive",
    "players_downed",
    "players_out",
    "players_status_unknown",
    "no_standing",
    "standing_hp_frac",
    "standing_hp_missing",
    "downed_bleed_frac",
    "downed_bleed_missing",
)
ELEVATION = (
    "elev_mean_dz",
    "elev_higher_than",
    "elev_lower_than",
    "elev_missing",
)
PRESSURE = (
    "fire_opponent_damage",
    "fire_attacking_squads",
    "prox_near_squads",
    "prox_medium_squads",
    "prox_occupied_sectors",
    "prox_nearest",
    "prox_nearest_missing",
    "stale_opponents_excluded",
    "squad_fresh_frac",
    "squad_fresh_missing",
)
RING_ACCESS = (
    "ring_outside_frac",
    "ring_boundary_rel",
    "ring_access_missing",
)

FEATURE_SETS = {
    "corrected_condition": CORRECTED_CONDITION,
    "condition_elevation": CORRECTED_CONDITION + ELEVATION,
    "condition_pressure": CORRECTED_CONDITION + PRESSURE,
    "condition_ring": CORRECTED_CONDITION + RING_ACCESS,
    "combined": CORRECTED_CONDITION + ELEVATION + PRESSURE + RING_ACCESS,
}

REPRESENTATION = (
    "Additive conditional logit. Each squad's score is a weighted sum of its own "
    "columns, then a softmax across the squads at that timestamp. Weights are "
    "unconstrained. Elevation is not multiplied by pressure, distance, or ring "
    "state, so the fit cannot express 'height matters only in this context'. "
    "A column shared by every squad at one timestamp cancels."
)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pool(rows: list[dict], *, shields: bool) -> tuple[float, float, int]:
    now = 0.0
    cap = 0.0
    used = 0
    for row in rows:
        health = _number(row["health"]["current"]["value"])
        health_max = _number(row["health"]["max"]["value"])
        if shields:
            shield = _number(row["shields"]["current"]["value"])
            shield_max = _number(row["shields"]["max"]["value"])
            parts = (health, health_max, shield, shield_max)
            if None in parts:
                continue
            now += parts[0] + parts[2]
            cap += parts[1] + parts[3]
        else:
            if health is None or health_max is None:
                continue
            now += health
            cap += health_max
        used += 1
    return now, cap, used


def corrected_health(snapshot: list[dict], team: Any) -> dict:
    """Standing combat health, and downed bleed, as separate columns.

    Standing combat health is health plus shields of ``alive`` players only.
    Downed bleed is health over max health of ``downed`` players only. A
    downed player's 100/100 bleed pool does not raise standing combat health.
    """
    mine = [row for row in snapshot if row["teamId"]["value"] == team]
    alive = [row for row in mine if row["status"]["value"] == "alive"]
    downed = [row for row in mine if row["status"]["value"] == "downed"]
    now, cap, used = _pool(alive, shields=True)
    bleed, bleed_cap, bleed_used = _pool(downed, shields=False)
    return {
        "standing_hp_frac": now / cap if used and cap > 0 else None,
        "standing_hp_missing": 0.0 if used and cap > 0 else 1.0,
        "downed_bleed_frac": bleed / bleed_cap if bleed_used and bleed_cap > 0 else None,
        "downed_bleed_missing": 0.0 if bleed_used and bleed_cap > 0 else 1.0,
    }


def _blank(names: tuple[str, ...]) -> dict:
    return {name: None for name in names}


def columns_from_measure(measured: dict) -> dict:
    """Flat columns from one ``tactical.measure`` result. Unknown stays None."""
    values = _blank(ELEVATION + PRESSURE + RING_ACCESS)
    if not measured.get("available"):
        values["elev_missing"] = 1.0
        values["prox_nearest_missing"] = 1.0
        values["squad_fresh_missing"] = 1.0
        values["ring_access_missing"] = 1.0
        return values

    elevation = measured["elevation"]
    if elevation.get("mean_dz") is None:
        values["elev_missing"] = 1.0
    else:
        values["elev_mean_dz"] = float(elevation["mean_dz"])
        values["elev_higher_than"] = float(elevation["higher_than"])
        values["elev_lower_than"] = float(elevation["lower_than"])
        values["elev_missing"] = 0.0

    fire = measured["fire"]
    pressure = measured["pressure"]
    values["fire_opponent_damage"] = float(fire["from_opponents"])
    values["fire_attacking_squads"] = float(fire["attacking_squads"])
    values["prox_near_squads"] = float(pressure["near_squads"])
    values["prox_medium_squads"] = float(pressure["medium_squads"])
    values["prox_occupied_sectors"] = float(pressure["occupied_sectors"])
    values["stale_opponents_excluded"] = float(measured["quality"]["opposing_standing_stale_excluded"])
    nearest = pressure["opponents"][0]["distance"] if pressure["opponents"] else None
    values["prox_nearest"] = float(nearest) if nearest is not None else None
    values["prox_nearest_missing"] = 0.0 if nearest is not None else 1.0
    standing = measured["quality"]["standing"]
    if standing:
        values["squad_fresh_frac"] = measured["quality"]["standing_with_fresh_position"] / standing
        values["squad_fresh_missing"] = 0.0
    else:
        values["squad_fresh_missing"] = 1.0

    ring = measured["ring"]
    measured_players = ring.get("players_measured") or 0
    radius = ring.get("radius")
    if ring.get("known") and measured_players and radius:
        values["ring_outside_frac"] = ring["outside_players"] / measured_players
        signed = ring.get("boundary_distance") or []
        values["ring_boundary_rel"] = (sum(signed) / len(signed)) / radius
        values["ring_access_missing"] = 0.0
    else:
        values["ring_access_missing"] = 1.0
    return values


def _coverage_add(coverage: dict, columns: dict) -> None:
    coverage["rows"] += 1
    coverage["elevation_known"] += columns["elev_missing"] == 0.0
    coverage["nearest_opponent_known"] += columns["prox_nearest_missing"] == 0.0
    coverage["ring_access_known"] += columns["ring_access_missing"] == 0.0
    coverage["standing_hp_known"] += columns["standing_hp_missing"] == 0.0
    coverage["fresh_position_known"] += columns["squad_fresh_missing"] == 0.0


def attach(groups: list[dict], records: list[dict], data_dir) -> tuple[list[dict], dict]:
    """Copy groups with corrected health and tactical columns at the same timestamps."""
    by_game: dict = {}
    for group in groups:
        by_game.setdefault(group["game_id"], []).append(group)
    raw = {record["game_id"]: record["raw_path"] for record in records}
    index = TacticalIndex(Path(data_dir))
    coverage = {
        "rows": 0,
        "elevation_known": 0,
        "nearest_opponent_known": 0,
        "ring_access_known": 0,
        "standing_hp_known": 0,
        "fresh_position_known": 0,
    }
    rewritten = []
    for game_id, items in by_game.items():
        payload = json.loads(Path(raw[game_id]).read_text(encoding="utf-8-sig"))
        replayed = replay_checkpoints(payload["series"])
        built = index._build(game_id)
        for group in items:
            snapshot = replayed["snapshots"].get(group["t"]) or []
            rows = []
            for row in group["rows"]:
                health = corrected_health(snapshot, row["team"])
                if built is None or group["t"] not in built["seconds"]:
                    tactical = {"available": False}
                else:
                    tactical = measure(built, group["t"], row["team"])
                columns = columns_from_measure(tactical)
                columns.update(health)
                _coverage_add(coverage, columns)
                features = {**row["features"], **columns}
                rows.append({**row, "features": features})
            rewritten.append({**group, "rows": rows})
    return rewritten, coverage
