"""Optional recorded-schedule preview. Imported only by the map display endpoint.

Nothing in the prediction or coach paths reads this index. The schedule is the
recorded state at t (including its t=0 plan), not a revision learned after t.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

from dashboard_data import _raw_path, _record
from overstat_patch import apply_frame, frame_entries, initial_state
from ring_overlay import display_geometry


class ObserverRingIndex:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._games = OrderedDict()
        self._lock = threading.Lock()

    def _build(self, game_id: int) -> dict:
        record = _record(self.data_dir, game_id)
        path = _raw_path(record, self.data_dir) if record else None
        if path is None:
            return {}
        series = json.loads(path.read_text(encoding="utf-8-sig")).get("series") or []
        state, observed, seconds = initial_state(), {}, {}
        playing = False
        for frame in frame_entries(series):
            t = frame["t"]
            feed = frame.get("f") or []
            state, observed, _ = apply_frame(state, frame.get("d"), feed if isinstance(feed, list) else [], observed, t)
            playing = playing or state.get("state") == "Playing"
            if not playing:
                continue
            schedule = state.get("rings")
            entries = [entry for entry in schedule if isinstance(entry, dict)] if isinstance(schedule, list) else []
            seconds[t] = display_geometry(entries, t, observer=True)
        return seconds

    def at(self, game_id: int, t: int) -> dict:
        with self._lock:
            if game_id not in self._games:
                self._games[game_id] = self._build(game_id)
            self._games.move_to_end(game_id)
            while len(self._games) > 2:
                self._games.popitem(last=False)
            return self._games[game_id].get(t) or {
                "t": t, "state": "unavailable", "observer_preview": True,
                "location": None, "target": None, "following_target": None,
                "label": "Recorded ring geometry unavailable",
            }
