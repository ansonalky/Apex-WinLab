"""One user-facing ring label for the map, the tactical panel and AI evidence.

Raw ``stage`` values are the replay's 0-based ring-entry indices (game 123633
has stages 0-5; stage 0 is the first closure). The two ring readers name a
*waiting* ring differently, though both draw the same circle:

- ``map_config.current_ring`` reports the next entry to close;
- ``ring_features.ring_at`` reports the last entry that closed.

While a ring closes, both report the closing entry. This module maps either
onto one numbering: ring N is the N-th closure (raw index N - 1). The raw
index is kept beside the label with its meaning, and neither reader changes.
"""

from __future__ import annotations

from typing import Any

# What a reader's raw stage refers to while the ring waits.
NEXT_TO_CLOSE = "next_to_close"
LAST_CLOSED = "last_closed"


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def ring_label(phase: str | None, stage_raw: Any, waiting_refers_to: str) -> dict:
    """``ring_number``, ``label`` and the raw index with its meaning.

    ``phase`` is ``closing`` or ``waiting``, or a reader's other states
    (``closing_unrecorded``, ``closing_target_unknown``, ``not_revealed``,
    ``none``, ``unavailable``, ``inconsistent``). ``ring_number`` is the ring
    closing now, or the next to close while waiting.
    """
    raw = _int(stage_raw)
    out = {"stage_raw": raw, "stage_raw_meaning": None, "ring_number": None, "rings_closed": None, "label": None}
    if phase in ("closing", "closing_unrecorded", "closing_target_unknown") and raw is not None:
        n = raw + 1
        out.update(stage_raw_meaning="0-based index of the ring closing now", ring_number=n, rings_closed=raw,
                   label=f"Ring {n} closing" + ("" if phase == "closing" else " (end not recorded)"))
    elif phase == "waiting" and raw is not None:
        closed = raw + 1 if waiting_refers_to == LAST_CLOSED else raw
        meaning = ("0-based index of the last ring that closed" if waiting_refers_to == LAST_CLOSED
                   else "0-based index of the next ring to close")
        out.update(stage_raw_meaning=meaning, ring_number=closed + 1, rings_closed=closed,
                   label=f"Ring {closed} closed · ring {closed + 1} next" if closed else "Waiting · ring 1 next")
    elif phase in ("not_revealed", "none"):
        out["label"] = "No ring closing yet" if phase == "not_revealed" else "No ring recorded at this time"
    else:
        out["label"] = "Ring not available at this time"
    return out
