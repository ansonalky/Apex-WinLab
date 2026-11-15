"""Explanatory tactical measurements for one squad at one replay second.

Not a model input. The frozen models and per-second predictions do not use
anything here, and nothing here changes a probability.

Built from one causal pass over the match's local replay (the same decoder
as the dashboard and features): at second ``t`` only state applied up to
``t`` and feed events that arrived at or before ``t`` are used. Ring geometry
comes from ``ring_features.ring_at`` over live (revealed) ring entries only,
so the planned future schedule is never used.

Coordinates are raw replay units. Their real-world scale is not verified
(see docs/tactical.md); distances are reported in those units.

Interpretation limits, stated with every response:
- A higher z is a height difference, not usable high-ground control.
- Proximity and bearing do not establish line of sight.
- Incoming damage is observed recent fire from feed events, not current
  visibility.
"""

from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from dashboard_data import _raw_path, _record
from features import STANDING
from overstat_patch import apply_frame, export_players, frame_entries, initial_state
from ring_features import live_entries, ring_at

CONTRACT_VERSION = 1
# A coordinate updated more than this many seconds ago is stale.
FRESH_GAP_S = 5
# Opposing squads are compared within these centroid distances (replay units).
NEAR_UNITS = 3000
MEDIUM_UNITS = 8000
# Incoming fire window, in seconds up to and including t.
FIRE_WINDOW_S = 10
SECTORS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# Below this centroid distance a bearing between squads is not meaningful.
MIN_BEARING_UNITS = 500
# Environmental damage has attacker "World", teamId 0, no position. The ring
# appears under localized weapon names.
RING_WEAPONS = {"The Ring", "圓圈", "安全区"}
CACHE_GAMES = 2

LIMITS = [
    "Coordinates are raw replay units; their real-world scale is not verified.",
    "A higher z is a height difference only. It does not establish usable high-ground control.",
    "Distance and bearing do not establish line of sight.",
    "Incoming fire is observed damage from feed events in the window, not current visibility.",
    "Ring values use only ring stages revealed by this second.",
    "Bearings are on the map image: north is up, east is right.",
]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def bearing(dx: float, dy: float) -> tuple[float, str]:
    """Map bearing from a vector in replay x/y (x east, y north), degrees clockwise from north."""
    degrees = math.degrees(math.atan2(dx, dy)) % 360
    return round(degrees, 1), SECTORS[int(((degrees + 22.5) % 360) // 45)]


def _player(row: dict) -> dict:
    pos = row["pos"]
    value = pos.get("value") or {}
    gap = pos.get("gap") or {}
    gaps = [g for g in (gap.get("x"), gap.get("y")) if isinstance(g, (int, float))]
    return {
        "name": row["name"]["value"],
        "team": row["teamId"]["value"],
        "status": row["status"]["value"],
        "x": _number(value.get("x")),
        "y": _number(value.get("y")),
        "z": _number(value.get("z")),
        "pos_age_s": max(gaps) if len(gaps) == 2 else None,
        "z_age_s": gap.get("z") if isinstance(gap.get("z"), (int, float)) else None,
    }


def _usable(player: dict) -> bool:
    return (
        player["status"] in STANDING
        and player["x"] is not None and player["y"] is not None
        and player["pos_age_s"] is not None and player["pos_age_s"] <= FRESH_GAP_S
    )


def _centroid(players: list[dict], key: str = "xy") -> tuple | None:
    if not players:
        return None
    if key == "z":
        zs = [p["z"] for p in players if p["z"] is not None and p["z_age_s"] is not None and p["z_age_s"] <= FRESH_GAP_S]
        return sum(zs) / len(zs) if zs else None
    return (sum(p["x"] for p in players) / len(players), sum(p["y"] for p in players) / len(players))


def _damage(entry_t: int, event: dict) -> dict | None:
    if not isinstance(event, dict) or event.get("type") != "damage":
        return None
    victim = event.get("victim") if isinstance(event.get("victim"), dict) else {}
    attacker = event.get("player") if isinstance(event.get("player"), dict) else {}
    vpos = victim.get("pos") or {}
    apos = attacker.get("pos") or {}
    world = not attacker.get("teamId")
    return {
        "source": ("ring" if event.get("weapon") in RING_WEAPONS else "environment") if world else "player",
        "t": entry_t,
        "victim_team": victim.get("teamId"),
        "victim": victim.get("name"),
        "victim_xy": (_number(vpos.get("x")), _number(vpos.get("y"))),
        "attacker_team": attacker.get("teamId"),
        "attacker_team_name": attacker.get("teamName"),
        "attacker_xy": (_number(apos.get("x")), _number(apos.get("y"))),
        "amount": _number(event.get("damage")),
        "weapon": event.get("weapon"),
    }


class TacticalIndex:
    """Per-match causal states and damage events, built once and cached."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._games: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _build(self, game_id: int) -> dict | None:
        record = _record(self.data_dir, game_id)
        path = _raw_path(record, self.data_dir) if record else None
        if path is None:
            return None
        series = json.loads(path.read_text(encoding="utf-8-sig")).get("series") or []
        state, observed = initial_state(), {}
        seconds: dict[int, dict] = {}
        damage: list[dict] = []
        start = None
        for frame in frame_entries(series):
            t = frame["t"]
            events = frame.get("f") or []
            events = events if isinstance(events, list) else []
            state, observed, _error = apply_frame(state, frame.get("d"), events, observed, t)
            for event in events:
                hit = _damage(t, event)
                if hit is not None:
                    damage.append(hit)
            if start is None:
                if state.get("state") != "Playing":
                    continue
                start = t
            seconds[t] = {
                "players": [_player(row) for row in export_players(state, observed, t)],
                "ring": ring_at(live_entries(state, observed), t),
                "phase": state.get("state"),
            }
        return {"playing_start": start, "seconds": seconds, "damage": damage}

    def game(self, game_id: int) -> dict | None:
        with self._lock:
            if game_id in self._games:
                self._games.move_to_end(game_id)
                return self._games[game_id]
        built = self._build(game_id)
        with self._lock:
            self._games[game_id] = built
            while len(self._games) > CACHE_GAMES:
                self._games.popitem(last=False)
        return built

    def at(self, game_id: int, t: int, team: Any) -> dict:
        game = self.game(game_id)
        if game is None or not game["seconds"]:
            return {"available": False, "reason": "no local replay for this match"}
        times = sorted(game["seconds"])
        t = max(times[0], min(t, times[-1]))
        return measure(game, t, team)


def measure(game: dict, t: int, team: Any) -> dict:
    second = game["seconds"][t]
    players = second["players"]
    team_key = str(team)
    mine = [p for p in players if str(p["team"]) == team_key]
    standing = [p for p in mine if p["status"] in STANDING]
    usable = [p for p in mine if _usable(p)]
    by_team: dict[str, list[dict]] = {}
    for p in players:
        if str(p["team"]) != team_key and _usable(p):
            by_team.setdefault(str(p["team"]), []).append(p)

    # Data quality: standing positions vs stale eliminated positions, kept apart.
    members = [
        {
            "name": p["name"],
            "status": p["status"],
            "position_used": _usable(p),
            "pos_age_s": p["pos_age_s"],
            "z_age_s": p["z_age_s"],
            "note": (
                "last known position of an eliminated or dead player; not used" if p["status"] not in STANDING
                else "position stale; not used" if not _usable(p) else ""
            ),
        }
        for p in mine
    ]
    quality = {
        "fresh_gap_s": FRESH_GAP_S,
        "members": members,
        "standing": len(standing),
        "standing_with_fresh_position": len(usable),
        "opposing_standing_with_fresh_position": sum(len(v) for v in by_team.values()),
        "opposing_standing_stale_excluded": sum(
            1 for p in players if str(p["team"]) != team_key and p["status"] in STANDING and not _usable(p)
        ),
    }

    center = _centroid(usable)
    my_z = _centroid(usable, "z")
    opponents = []
    if center is not None:
        for other, group in by_team.items():
            oc = _centroid(group)
            dx, dy = oc[0] - center[0], oc[1] - center[1]
            distance = math.hypot(dx, dy)
            deg, sector = bearing(dx, dy) if distance >= MIN_BEARING_UNITS else (None, None)
            oz = _centroid(group, "z")
            opponents.append({
                "team": other,
                "players_used": len(group),
                "distance": round(distance),
                "band": "near" if distance <= NEAR_UNITS else "medium" if distance <= MEDIUM_UNITS else "far",
                "bearing_deg": deg,
                "sector": sector,
                "dz": round(my_z - oz) if my_z is not None and oz is not None else None,
            })
        opponents.sort(key=lambda o: o["distance"])

    nearby = [o for o in opponents if o["band"] != "far"]
    elevation = {
        "available": center is not None and my_z is not None,
        "squad_z": round(my_z) if my_z is not None else None,
        "comparison_distance_units": MEDIUM_UNITS,
        "opponents_compared": sum(1 for o in nearby if o["dz"] is not None),
        "higher_than": sum(1 for o in nearby if o["dz"] is not None and o["dz"] > 0),
        "lower_than": sum(1 for o in nearby if o["dz"] is not None and o["dz"] < 0),
        "mean_dz": round(sum(o["dz"] for o in nearby if o["dz"] is not None) / max(1, sum(1 for o in nearby if o["dz"] is not None)))
        if any(o["dz"] is not None for o in nearby) else None,
        "note": "Positive dz: this squad's mean z is higher. Height only; not high-ground control or line of sight.",
    }

    sectors = {s: 0 for s in SECTORS}
    for o in nearby:
        if o["sector"] is not None:
            sectors[o["sector"]] += 1
    pressure = {
        "near_units": NEAR_UNITS,
        "medium_units": MEDIUM_UNITS,
        "near_squads": sum(1 for o in opponents if o["band"] == "near"),
        "medium_squads": sum(1 for o in opponents if o["band"] == "medium"),
        "sectors": sectors,
        "occupied_sectors": sum(1 for v in sectors.values() if v),
        "too_close_for_bearing": sum(1 for o in nearby if o["sector"] is None),
        "min_bearing_units": MIN_BEARING_UNITS,
        "opponents": opponents[:8],
        "note": "Opposing squads with fresh standing positions, by centroid distance. Not line of sight.",
    }

    window = [d for d in game["damage"] if t - FIRE_WINDOW_S < d["t"] <= t and str(d["victim_team"]) == team_key]
    attackers: dict[str, dict] = {}
    by_source = {"ring": 0.0, "environment": 0.0, "self": 0.0}
    for hit in window:
        amount = hit["amount"] or 0.0
        if hit["source"] != "player":
            by_source[hit["source"]] += amount
            continue
        if str(hit["attacker_team"]) == team_key:
            by_source["self"] += amount
            continue
        row = attackers.setdefault(str(hit["attacker_team"]), {
            "team": str(hit["attacker_team"]), "team_name": hit["attacker_team_name"], "damage": 0.0, "hits": 0,
            "last_hit_t": None, "bearings": [],
        })
        row["damage"] += amount
        row["hits"] += 1
        row["last_hit_t"] = hit["t"]
        (vx, vy), (ax, ay) = hit["victim_xy"], hit["attacker_xy"]
        if None not in (vx, vy, ax, ay):
            row["bearings"].append(bearing(ax - vx, ay - vy))
    for row in attackers.values():
        degrees = [b[0] for b in row.pop("bearings")]
        if degrees:
            sx = sum(math.sin(math.radians(d)) for d in degrees)
            cy = sum(math.cos(math.radians(d)) for d in degrees)
            row["bearing_deg"], row["sector"] = bearing(sx, cy)
        else:
            row["bearing_deg"], row["sector"] = None, None
        row["damage"] = round(row["damage"])
        row["seconds_ago"] = t - row["last_hit_t"]
    fire = {
        "window_s": FIRE_WINDOW_S,
        "damage_events": len(window),
        "damage_taken": round(sum((h["amount"] or 0.0) for h in window)),
        "from_opponents": round(sum(r["damage"] for r in attackers.values())),
        "from_ring": round(by_source["ring"]),
        "from_other_environment": round(by_source["environment"]),
        "from_own_squad": round(by_source["self"]),
        "attacking_squads": len(attackers),
        "attackers": sorted(attackers.values(), key=lambda r: -r["damage"]),
        "note": "Observed damage events in the window, with attacker direction at the time of each hit. Not current visibility.",
    }

    ring = second["ring"]
    ring_out = {"known": bool(ring.get("known")), "phase": ring.get("phase"), "stage": ring.get("stage"),
                "closing_remaining_s": ring.get("closing_remaining_s"), "reason": ring.get("reason")}
    if ring.get("known") and usable:
        cx, cy = ring["center"]
        r = ring["radius"]
        signed = [math.hypot(p["x"] - cx, p["y"] - cy) - r for p in usable]
        ring_out.update({
            "radius": round(r),
            "boundary_distance": [round(d) for d in signed],
            "nearest_inside_margin": round(-max(signed)) if max(signed) <= 0 else None,
            "outside_players": sum(1 for d in signed if d > 0),
            "players_measured": len(signed),
            "note": "Signed distance to the boundary per member; positive is outside.",
        })

    return {
        "available": True,
        "contract_version": CONTRACT_VERSION,
        "t": t,
        "elapsed": t - game["playing_start"] if game["playing_start"] is not None else None,
        "team": team_key,
        "explanatory_only": True,
        "limits": LIMITS,
        "quality": quality,
        "elevation": elevation,
        "fire": fire,
        "pressure": pressure,
        "ring": ring_out,
    }
