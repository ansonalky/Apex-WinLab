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
from match_inference import NEW_MATCH, build_table, describe, persist_new_match
from train_model import contention_baseline, masked, predict

HELD_OUT = "held_out"
FINAL = "final_model_fitted"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LivePredictions:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._tables: dict[tuple, dict] = {}
        self._models: dict[str, dict] = {}
        self._described: dict[Any, dict] = {}
        self._inflight: set = set()
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
        if kind == NEW_MATCH:
            from match_inference import incompatible_reason

            if incompatible_reason(model):
                return None
        self._models[kind] = {
            "model": model,
            "provenance": {
                "kind": kind,
                "file": path.relative_to(self.data_dir).as_posix(),
                "sha256": _sha256(path),
                "sha256_matches_bundle": True if kind == HELD_OUT else None,
                "fit_on": model.get("role") or (
                    "train + validation sessions" if kind == HELD_OUT
                    else "the original 51 games" if kind == NEW_MATCH
                    else "all accepted games"
                ),
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

    def inference(self, game_id: Any) -> dict:
        """Membership and storage for one game. Does not run the model."""
        if game_id not in self._described:
            self._described[game_id] = describe(self.data_dir, game_id)
        return self._described[game_id]

    def _table_keys(self, kind: str, game_id: Any) -> tuple:
        if kind != NEW_MATCH:
            return (kind, game_id), ("meta", kind, game_id)
        info = self.inference(game_id)
        identity = (info.get("telemetry_sha256"), info.get("model_sha256"), info.get("feature_code_version"))
        return (kind, game_id, identity), ("meta", kind, game_id, identity)

    def available(self, game_id: Any, kind: str) -> bool:
        if kind == NEW_MATCH:
            return bool(self.inference(game_id).get("predict")) and self.model(kind) is not None
        if kind == HELD_OUT and game_id not in self.test_games:
            return False
        # Frozen held-out and fitted labels stay on the original 51 games.
        record = _record(self.data_dir, game_id)
        if record is not None and not frozen_model_applies(self.data_dir, record):
            return False
        return self.model(kind) is not None

    def ready(self, game_id: Any, kind: str) -> bool:
        key, _meta = self._table_keys(kind, game_id)
        with self._lock:
            return key in self._tables

    def schedule(self, game_id: Any) -> None:
        """Compute a new-match table in the background. Seeking does not wait."""
        if not self.available(game_id, NEW_MATCH):
            return
        key, _meta = self._table_keys(NEW_MATCH, game_id)
        with self._lock:
            if key in self._tables or key in self._inflight:
                return
            self._inflight.add(key)

        def run() -> None:
            try:
                self.table(game_id, NEW_MATCH)
                self._persist(game_id)
            finally:
                with self._lock:
                    self._inflight.discard(key)

        threading.Thread(target=run, name=f"new-match-{game_id}", daemon=True).start()

    def _persist(self, game_id: Any) -> None:
        info = self.inference(game_id)
        if info.get("storage") != "saved" or info.get("membership") == "original":
            return
        key, meta_key = self._table_keys(NEW_MATCH, game_id)
        with self._lock:
            table = self._tables.get(key)
            meta = self._tables.get(meta_key) or {}
        if not table or not info.get("provenance"):
            return
        try:
            from import_tiger import connect

            connection = connect()
        except (SystemExit, Exception):
            return
        try:
            persist_new_match(
                connection,
                int(game_id),
                table,
                provenance=info["provenance"],
                replay_match_start=meta.get("replay_match_start"),
            )
        except Exception:
            return
        finally:
            connection.close()

    def table(self, game_id: Any, kind: str) -> dict[int, dict]:
        key, meta_key = self._table_keys(kind, game_id)
        with self._lock:
            if key in self._tables:
                return self._tables[key]
        entry = self.model(kind)
        record = _record(self.data_dir, game_id)
        path = _raw_path(record, self.data_dir) if record else None
        if entry is None or path is None:
            return {}
        series = json.loads(path.read_text(encoding="utf-8-sig")).get("series") or []
        if kind == NEW_MATCH:
            built = build_table(series, entry["model"])
            table = built["table"]
            table_meta = {
                "cutoff_t": built.get("cutoff_t"),
                "playing_start": built.get("playing_start"),
                "replay_match_start": built.get("replay_match_start"),
                "reason": built.get("reason"),
            }
            with self._lock:
                self._tables[key] = table
                self._tables[meta_key] = table_meta
            return table
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
            self._tables[meta_key] = table_meta
        return table

    def _point(self, game_id: Any, t: int, kind: str) -> dict:
        if not self.available(game_id, kind):
            info = self.inference(game_id) if kind == NEW_MATCH else {}
            return {"available": False, "reason": info.get("reason"), "provenance": info.get("provenance")}
        _key, meta_key = self._table_keys(kind, game_id)
        table = self.table(game_id, kind)
        meta = self._tables.get(meta_key) or {}
        provenance = self._models[kind]["provenance"]
        if kind == NEW_MATCH and self.inference(game_id).get("provenance"):
            provenance = self.inference(game_id)["provenance"]
        point = table.get(t)
        if point is None:
            cutoff = meta.get("cutoff_t")
            reason = meta.get("reason") or (
                "after the feature cut-off; no prediction is made once the game is decided"
                if cutoff is not None and t >= cutoff else "no prediction at this second"
            )
            return {"available": True, "point": None, "reason": reason, "cutoff_t": cutoff, "provenance": provenance}
        return {"available": True, "point": point, "cutoff_t": meta.get("cutoff_t"), "provenance": provenance}

    def at(self, game_id: Any, t: int, kind: str) -> dict:
        """The prediction made at exactly second ``t``, or why there is none."""
        return self._point(game_id, t, kind)

    def peek(self, game_id: Any, t: int) -> dict:
        """New-match estimate if it is ready. Otherwise start it and report loading."""
        info = self.inference(game_id)
        if not info.get("predict"):
            return {
                "available": False,
                "status": "unavailable",
                "reason": info.get("reason"),
                "provenance": info.get("provenance"),
                "limitation": info.get("limitation"),
            }
        if not self.ready(game_id, NEW_MATCH):
            self.schedule(game_id)
            return {
                "available": True,
                "status": "loading",
                "point": None,
                "reason": "Computing win estimates from the saved model.",
                "provenance": info.get("provenance"),
                "limitation": info.get("limitation"),
                "membership": info.get("membership"),
            }
        result = self._point(game_id, t, NEW_MATCH)
        result["status"] = "ready"
        result["limitation"] = info.get("limitation")
        result["membership"] = info.get("membership")
        return result
