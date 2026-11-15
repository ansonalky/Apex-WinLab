"""Ring-relative squad features for the position-feature v2 experiment.

Separate from ``features.py``; the frozen v1 model and its reports do not use
this module.

Ring semantics, verified on all 51 accepted games against ring-damage
victim positions (see docs/ring-features-v2.md):

- ``rings[]`` entries carry ``stage``, ``start``, ``end``, ``startCenter``,
  ``endCenter``, ``startSize``, ``endSize``. Sizes are radii.
- While a stage closes (``start <= t < end``) center and radius move linearly
  from start to end. Between closings the ring is the last stage's end circle.

What counts as revealed at checkpoint ``t``:

- The ``t = 0`` state holds the whole planned ring schedule, including future
  centers, and that plan can be revised mid-match. It is never used.
- A stage counts only once its live entry has been inserted (observation
  stamp > 0), which happens when that stage starts closing.
- Before the first live entry, ring geometry is unknown. During a closing
  whose live entry has no ``end``/``endCenter`` (usually the final ring),
  geometry is unknown. Time until the next closing exists only in the
  revisable plan, so it is unknown everywhere.

Unknown values are ``None`` with a ``*_missing`` flag. Proximity is reported
as distance only; it says nothing about cover or line of sight.
"""

from __future__ import annotations

import math
from typing import Any

from features import STANDING
from overstat_patch import apply_frame, export_players, frame_entries, initial_state

# An opposing squad counts as "nearby" when its standing centroid is within
# this many ring radii of ours. Ring-relative, because coordinate units are
# unverified and the useful scale shrinks with the ring.
NEARBY_RING_RADII = 0.2
GEOMETRY_KEYS = ("start", "end", "startCenter", "endCenter", "startSize", "endSize")

RING_FEATURES = (
    "ring_center_dist_rel",
    "ring_boundary_signed_rel",
    "ring_outside_frac",
    "ring_spread_rel",
    "ring_nearest_enemy_rel",
    "ring_nearby_enemies",
    "ring_outside_frac_closing",
    "ring_boundary_signed_closing",
    "ring_geometry_missing",
    "ring_squad_position_missing",
)


def live_entries(state: dict, observed: dict) -> list[dict]:
    """Ring entries inserted during the match (stamped after t 0), oldest first."""
    rings = state.get("rings")
    stamps = observed.get("rings") if isinstance(observed, dict) else None
    if not isinstance(rings, list) or not isinstance(stamps, list):
        return []
    live = []
    for entry, stamp in zip(rings, stamps):
        if not isinstance(entry, dict) or not isinstance(stamp, dict):
            continue
        seen = stamp.get("stage")
        if isinstance(seen, int) and seen > 0:
            live.append((seen, entry))
    live.sort(key=lambda item: (item[0], item[1].get("stage", 0)))
    return [entry for _seen, entry in live]


def ring_at(entries: list[dict], t: int) -> dict:
    """Current ring from revealed entries: center, radius, phase, closing time left."""
    if not entries:
        return {"known": False, "phase": "not_revealed", "reason": "no live ring entry yet"}
    entry = entries[-1]
    start = entry.get("start")
    end = entry.get("end")
    if isinstance(start, (int, float)) and t < start:
        return {"known": False, "phase": "inconsistent", "reason": "latest live entry starts after t"}
    if not all(key in entry for key in GEOMETRY_KEYS):
        return {
            "known": False,
            "phase": "closing_target_unknown",
            "stage": entry.get("stage"),
            "reason": "live entry has no end time or target center",
        }
    if t < end:
        f = (t - start) / (end - start) if end > start else 1.0
        sc, ec = entry["startCenter"], entry["endCenter"]
        return {
            "known": True,
            "phase": "closing",
            "stage": entry.get("stage"),
            "center": (sc["x"] + f * (ec["x"] - sc["x"]), sc["y"] + f * (ec["y"] - sc["y"])),
            "radius": entry["startSize"] + f * (entry["endSize"] - entry["startSize"]),
            "closing_remaining_s": end - t,
        }
    ec = entry["endCenter"]
    return {
        "known": True,
        "phase": "waiting",
        "stage": entry.get("stage"),
        "center": (ec["x"], ec["y"]),
        "radius": entry["endSize"],
        "closing_remaining_s": None,
    }


def ring_target(entries: list[dict], ring: dict) -> dict | None:
    """The circle the ring is closing toward, for the map only (not a feature).

    The stream inserts a stage's live entry when that stage starts closing, and
    the entry already carries its target (``endCenter``, ``endSize``). Across
    the 100 local replays no target was revised after insertion, and no entry
    for the next stage exists while the ring waits. So a target is known only
    while a recorded stage closes; while waiting it is unknown, never taken
    from the t 0 plan.
    """
    if ring.get("phase") != "closing" or not entries:
        return None
    entry = entries[-1]
    center = entry["endCenter"]
    return {"x": center["x"], "y": center["y"], "r": entry["endSize"], "stage": entry.get("stage")}


def _position(row: dict) -> tuple[float, float] | None:
    x, y = row["pos"]["value"]["x"], row["pos"]["value"]["y"]
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def squad_ring_features(snapshot: list[dict], ring: dict) -> dict[Any, dict]:
    """Ring features per ``teamId``. Squads with no standing player get unknowns."""
    squads: dict[Any, list[tuple[float, float]]] = {}
    for row in snapshot:
        team = row["teamId"]["value"]
        if team is None:
            continue
        squads.setdefault(team, [])
        if row["status"]["value"] in STANDING:
            point = _position(row)
            if point is not None:
                squads[team].append(point)
    centroids = {
        team: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None
        for team, pts in squads.items()
    }
    closing = 1.0 if ring.get("phase") == "closing" else 0.0
    out = {}
    for team, points in squads.items():
        values = {name: None for name in RING_FEATURES}
        values["ring_geometry_missing"] = 0.0 if ring.get("known") else 1.0
        values["ring_squad_position_missing"] = 0.0 if points else 1.0
        if ring.get("known") and points and ring["radius"] > 0:
            r = ring["radius"]
            cx, cy = ring["center"]
            centroid = centroids[team]
            dists = [math.hypot(x - cx, y - cy) for x, y in points]
            others = [math.hypot(centroid[0] - c[0], centroid[1] - c[1]) for t2, c in centroids.items() if t2 != team and c]
            values["ring_center_dist_rel"] = math.hypot(centroid[0] - cx, centroid[1] - cy) / r
            values["ring_boundary_signed_rel"] = sum((d - r) / r for d in dists) / len(dists)
            values["ring_outside_frac"] = sum(1 for d in dists if d > r) / len(dists)
            values["ring_spread_rel"] = sum(math.hypot(x - centroid[0], y - centroid[1]) for x, y in points) / len(points) / r
            values["ring_nearest_enemy_rel"] = min(others) / r if others else None
            values["ring_nearby_enemies"] = float(sum(1 for d in others if d < NEARBY_RING_RADII * r))
            values["ring_outside_frac_closing"] = values["ring_outside_frac"] * closing
            values["ring_boundary_signed_closing"] = values["ring_boundary_signed_rel"] * closing
        out[team] = values
    return out


def ring_snapshots(series: list[dict], times: set[int]) -> dict[int, dict]:
    """Causal pass: at each ``t`` in ``times``, the revealed ring and per-squad features."""
    state = initial_state()
    observed: dict = {}
    out = {}
    for frame in frame_entries(series):
        t = frame["t"]
        events = frame.get("f") or []
        state, observed, _error = apply_frame(state, frame.get("d"), events if isinstance(events, list) else [], observed, t)
        if t in times:
            ring = ring_at(live_entries(state, observed), t)
            out[t] = {"ring": ring, "squads": squad_ring_features(export_players(state, observed, t), ring)}
    return out
