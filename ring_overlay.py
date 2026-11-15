"""Map-only ring geometry from entries already revealed at the displayed second.

The tactical/model reader is unchanged. A live upcoming entry may arrive before
it starts closing; it must not replace the current boundary in the map snapshot.
"""
from __future__ import annotations

from ring_features import center_xy, finite_number, ring_at
from ring_label import LAST_CLOSED, NEXT_TO_CLOSE


def _circle(entry: dict, center_key: str, size_key: str) -> dict | None:
    center = center_xy(entry.get(center_key))
    radius = finite_number(entry.get(size_key))
    if center is None or radius is None or radius < 0:
        return None
    return {"x": center[0], "y": center[1], "r": radius, "stage": entry.get("stage")}


def overlay_geometry(entries: list[dict], t: int) -> tuple[dict, dict | None, str]:
    """Current safe boundary, next revealed target, shared waiting-label convention.

    ``entries`` must come from live_entries of the causal state at t, never from
    the final state or the t=0 plan. Only the immediately next stage is eligible.
    """
    started = [entry for entry in entries
               if finite_number(entry.get("start")) is not None and entry["start"] <= t]
    if started:
        active = max(started, key=lambda entry: entry["start"])
        ring = ring_at([active], t)
        convention = LAST_CLOSED
        stage = ring.get("stage")
        if not ring.get("known") or not isinstance(stage, int) or isinstance(stage, bool):
            return ring, None, convention
        target_stage = stage if ring["phase"] == "closing" else stage + 1
    else:
        # A first live entry can also reveal a countdown before ring 1 closes.
        first = next((entry for entry in entries if entry.get("stage") == 0), None)
        boundary = _circle(first, "startCenter", "startSize") if first else None
        start = finite_number(first.get("start")) if first else None
        if boundary is None or start is None or t >= start:
            return ring_at([], t), None, LAST_CLOSED
        ring = {"known": True, "phase": "waiting", "stage": 0,
                "center": (boundary["x"], boundary["y"]), "radius": boundary["r"],
                "closing_remaining_s": None}
        target_stage, convention = 0, NEXT_TO_CLOSE
    candidate = next((entry for entry in entries if entry.get("stage") == target_stage), None)
    if candidate is None:
        return ring, None, convention
    # Preserve incomplete-final-ring handling. No guessed end or geometry.
    start = finite_number(candidate.get("start"))
    if start is None or not ring_at([candidate], start).get("known"):
        return ring, None, convention
    return ring, _circle(candidate, "endCenter", "endSize"), convention


def following_destination(entries: list[dict], target: dict | None) -> dict | None:
    """The stage after the active destination, never a duplicate or skipped stage."""
    if target is None:
        return None
    stage = target.get("stage")
    if not isinstance(stage, int) or isinstance(stage, bool):
        return None
    candidate = next((entry for entry in entries if entry.get("stage") == stage + 1), None)
    if candidate is None:
        return None
    start = finite_number(candidate.get("start"))
    if start is None or not ring_at([candidate], start).get("known"):
        return None
    return _circle(candidate, "endCenter", "endSize")


def display_geometry(entries: list[dict], t: int, observer: bool = False) -> dict:
    """Serialize display geometry. Observer callers supply recorded schedule entries."""
    from ring_label import ring_label

    ring, target, convention = overlay_geometry(entries, t)
    return {
        "t": t, "state": ring.get("phase"), "observer_preview": observer,
        "location": {"x": ring["center"][0], "y": ring["center"][1], "r": ring["radius"]} if ring.get("known") else None,
        "target": target,
        "following_target": following_destination(entries, target),
        "countdown_s": ring.get("closing_remaining_s"),
        **ring_label(ring.get("phase"), ring.get("stage"), convention),
    }
