"""Per-second win probabilities from the frozen models. No refit, no tuning.

Each second from Playing start until the feature cut-off gets its own
prediction, made the way the evaluation made its 30-second ones:

- features: ``features.replay_checkpoints(series, step=1)`` and
  ``features.squad_rows``, the training code, run on the local replay file;
- model: ``held_out`` uses the frozen ``evaluation_model.json`` from the
  validated held-out bundle (train + validation fit, with its own scaler),
  and only for that bundle's test games. ``final_model_fitted`` uses
  ``models/model.json`` and is in-sample on every accepted game;
- eligibility: ``features.contention`` with no winner, so only elimination
  events and ``teamsAlive`` up to that second are used. The frozen 30-second
  evaluation also excluded checkpoints where the eventual winner was marked
  eliminated; that check needs the outcome, so it is not applied here;
- ``train_model.predict`` for raw probabilities, ``train_model.masked`` for
  the eligibility-constrained ones, ``contention_baseline`` for the baseline.

No probability is interpolated. Seconds after the cut-off (first second
that is not Playing, or ``teamsAlive <= 1``) have no prediction.

Results are cached in memory per game and model.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from dashboard_data import HELDOUT_BUNDLE, _model_file, _raw_path, _record, frozen_model_applies, load_heldout_bundle
from features import contention, elimination_team, replay_checkpoints, squad_rows
from train_model import contention_baseline, masked, predict

HELD_OUT = "held_out"
FINAL = "final_model_fitted"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LivePredictions:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._tables: dict[tuple[str, Any], dict[int, dict]] = {}
        self._models: dict[str, dict] = {}
        self.bundle = load_heldout_bundle(data_dir)
        self.test_games = {game for game, split in (self.bundle.get("split_of") or {}).items() if split == "test"}
        self._eliminations: dict[Any, dict] = {}

    def model(self, kind: str) -> dict | None:
        """The frozen model and where it came from, or None."""
        if kind in self._models:
            return self._models[kind]
        if kind == HELD_OUT:
            if not self.bundle.get("available"):
                return None
            path = self.data_dir / "exports" / HELDOUT_BUNDLE / "evaluation_model.json"
            expected = ((self.bundle["meta"].get("files") or {}).get("evaluation_model.json") or {}).get("sha256")
            if not path.is_file() or _sha256(path) != expected:
                return None
        else:
            path = _model_file(self.data_dir)
            expected = None
            if not path.is_file():
                return None
        model = json.loads(path.read_text(encoding="utf-8"))
        self._models[kind] = {
            "model": model,
            "provenance": {
                "kind": kind,
                "file": path.relative_to(self.data_dir).as_posix(),
                "sha256": _sha256(path),
                "sha256_matches_bundle": True if kind == HELD_OUT else None,
                "fit_on": model.get("role") or ("train + validation sessions" if kind == HELD_OUT else "all accepted games"),
                "l2": model.get("l2"),
                "features": model.get("features"),
                "scaler": "the model file's own (fit on its training rows)",
                "features_from": "features.replay_checkpoints(step=1) + squad_rows on the local replay",
                "sampling": "every replay second from Playing start to the cut-off",
            },
        }
        return self._models[kind]

    def squad_eliminations(self, game_id: Any) -> dict:
        """Replay second of each squad's ``eliminated`` feed event, for display.

        Only unambiguous, first events count. No squad in the 51 games was
        respawned after its squad event, so it is treated as final.
        """
        if game_id in self._eliminations:
            return self._eliminations[game_id]
        record = _record(self.data_dir, game_id)
        path = _raw_path(record, self.data_dir) if record else None
        found: dict = {}
        if path is not None:
            for entry in json.loads(path.read_text(encoding="utf-8-sig")).get("series") or []:
                for event in entry.get("f") or []:
                    team, problem = elimination_team(event)
                    if team is not None and not problem:
                        found.setdefault(team, entry.get("t"))
        with self._lock:
            self._eliminations[game_id] = found
        return found

    def available(self, game_id: Any, kind: str) -> bool:
        if kind == HELD_OUT and game_id not in self.test_games:
            return False
        # Expansion games: the frozen models were never fitted or evaluated on them.
        record = _record(self.data_dir, game_id)
        if record is not None and not frozen_model_applies(self.data_dir, record):
            return False
        return self.model(kind) is not None

    def table(self, game_id: Any, kind: str) -> dict[int, dict]:
        key = (kind, game_id)
        with self._lock:
            if key in self._tables:
                return self._tables[key]
        entry = self.model(kind)
        record = _record(self.data_dir, game_id)
        path = _raw_path(record, self.data_dir) if record else None
        if entry is None or path is None:
            return {}
        series = json.loads(path.read_text(encoding="utf-8-sig")).get("series") or []
        replayed = replay_checkpoints(series, 1)
        table = {}
        for t in sorted(replayed["snapshots"]):
            rows = squad_rows(replayed["snapshots"][t])
            keys = [row["team"] for row in rows]
            meta = replayed["meta"][t]
            status = contention(meta, keys, None)
            for row, flag in zip(rows, status["flags"]):
                row["eliminated"] = flag
            group = {"rows": rows, "elimination_verified": status["verified"]}
            raw = predict(entry["model"], group)
            constrained = masked(raw, group)
            baseline = contention_baseline(group)
            table[t] = {
                "t": t,
                "elapsed": meta["elapsed"],
                "eligibility": "verified" if status["verified"] else "unverified",
                "eligibility_reasons": status["reasons"],
                "elimination_verified": status["verified"],
                "in_contention": sum(1 for row in rows if not row["eliminated"]) if status["verified"] else len(rows),
                "squads": [
                    {
                        "teamId": row["team"],
                        "team_name": row.get("team_name"),
                        "eliminated": row["eliminated"],
                        "model": c,
                        "model_unconstrained": r,
                        "uniform_contention": b,
                        "features": row["features"],
                    }
                    for row, r, c, b in zip(rows, raw, constrained, baseline)
                ],
            }
        table_meta = {"cutoff_t": replayed["cutoff"], "playing_start": replayed["playing_start"]}
        with self._lock:
            self._tables[key] = table
            self._tables[("meta", kind, game_id)] = table_meta
        return table

    def at(self, game_id: Any, t: int, kind: str) -> dict:
        """The prediction made at exactly second ``t``, or why there is none."""
        if not self.available(game_id, kind):
            return {"available": False}
        table = self.table(game_id, kind)
        meta = self._tables.get(("meta", kind, game_id)) or {}
        point = table.get(t)
        if point is None:
            cutoff = meta.get("cutoff_t")
            reason = (
                "after the feature cut-off; no prediction is made once the game is decided"
                if cutoff is not None and t >= cutoff else "no prediction at this second"
            )
            return {"available": True, "point": None, "reason": reason, "cutoff_t": cutoff}
        return {"available": True, "point": point, "cutoff_t": meta.get("cutoff_t"),
                "provenance": self._models[kind]["provenance"]}
