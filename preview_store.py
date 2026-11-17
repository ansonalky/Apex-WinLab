"""Temporary Overstat previews. They are not accepted games and not database rows.

Files live under the configured root, one directory per game:

    <root>/<game_id>/meta.json
    <root>/<game_id>/raw/<livedata_id>.timeseries.json

``meta.json`` records ``expires_at``. Startup and each open delete expired
directories. Nothing here writes to Tiger Data or to an accepted manifest.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

TTL_SECONDS = 24 * 60 * 60

_ROOT: Path | None = None


def configure(root: Path) -> None:
    global _ROOT
    _ROOT = Path(root)


def configured() -> Path | None:
    return _ROOT


def cleanup(now: float | None = None) -> list[int]:
    """Delete previews whose ``expires_at`` has passed. Returns their game ids."""
    root = _ROOT
    if root is None or not root.is_dir():
        return []
    now = time.time() if now is None else now
    removed = []
    for child in root.iterdir():
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expires = float(meta.get("expires_at") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if expires <= now:
            shutil.rmtree(child, ignore_errors=True)
            try:
                removed.append(int(child.name))
            except ValueError:
                pass
    return removed


def write_preview(game_id: int, record: dict, raw: bytes, *, now: float | None = None) -> Path:
    """Store one replay and return its directory. Replaces an older preview of the same id."""
    if _ROOT is None:
        raise RuntimeError("preview store is not configured")
    now = time.time() if now is None else now
    directory = _ROOT / str(int(game_id))
    if directory.exists():
        shutil.rmtree(directory)
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True)
    livedata = record.get("livedata_id") or str(game_id)
    filename = f"{livedata}.timeseries.json"
    (raw_dir / filename).write_bytes(raw)
    stored = {
        "game_id": int(game_id),
        "session_id": record.get("session_id"),
        "event": record.get("event"),
        "map_name": record.get("map_name"),
        "quality_score": record.get("quality_score"),
        "organizer_username": record.get("organizer_username"),
        "match_start": record.get("match_start"),
        "livedata_id": livedata,
        "mid": record.get("mid"),
        "sha256": record.get("sha256"),
        "reason": record.get("reason") or "temporary_preview",
        "acceptance_status": "preview",
        "validation": record.get("validation"),
        "download_path": f"raw/{filename}",
        "expires_at": now + TTL_SECONDS,
        "ttl_seconds": TTL_SECONDS,
    }
    (directory / "meta.json").write_text(json.dumps(stored, sort_keys=True) + "\n", encoding="utf-8")
    return directory


def preview_record(game_id: Any) -> dict | None:
    """A live preview record, or None. Expired directories are removed."""
    root = _ROOT
    if root is None:
        return None
    try:
        game_id = int(game_id)
    except (TypeError, ValueError):
        return None
    directory = root / str(game_id)
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        expires = float(meta.get("expires_at") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if expires <= time.time():
        shutil.rmtree(directory, ignore_errors=True)
        return None
    return dict(meta) | {"_data_dir": str(directory), "_collection": "preview", "_preview": True}


def contains(game_id: Any) -> bool:
    return preview_record(game_id) is not None


def _timeline(game_id: int) -> dict | None:
    from dashboard_data import build_timeline

    record = preview_record(game_id)
    if record is None:
        return None
    path = Path(record["_data_dir"]) / record["download_path"]
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    built = build_timeline(payload.get("series") or [])
    from dashboard_data import _squads

    return {
        "record": record,
        "playing_start": built["playing_start"],
        "replay_match_start": built["replay_match_start"],
        "squads": _squads(built["frames"]),
        "frames": built["frames"],
        "conclusion_t": built["conclusion_t"],
    }


class PreviewStore:
    """Read API for one temporary preview. Not a collection and not a database."""

    def summary(self, game_id: int) -> dict | None:
        from dashboard_data import COORDINATE_NOTE, replay_range

        built = _timeline(game_id)
        if built is None:
            return None
        record = built["record"]
        winner = ((record.get("validation") or {}).get("winner") or {}) if isinstance(record.get("validation"), dict) else {}
        return {
            "game_id": int(game_id),
            "session_id": record.get("session_id"),
            "event": record.get("event"),
            "map_name": record.get("map_name"),
            "quality_score": record.get("quality_score"),
            "organizer_username": record.get("organizer_username"),
            "collection": "preview",
            "storage": "preview",
            # Why the replay was not saved: the collector's reason, and the replay
            # check's own detail when that check failed.
            "acceptance_reason": record.get("reason"),
            "acceptance_detail": (record.get("validation") or {}).get("detail") if isinstance(record.get("validation"), dict) else None,
            "winner": {
                "teamId": winner.get("teamId"),
                "teamName": winner.get("teamName"),
                "label_source": winner.get("label_source"),
            },
            "playing_start": built["playing_start"],
            "frame_count": len(built["frames"]),
            "times": [{"t": frame["t"], "elapsed": frame["elapsed"]} for frame in built["frames"]],
            **replay_range(built["frames"], built["playing_start"], built.get("conclusion_t")),
            "squads": built["squads"],
            "coordinate_note": COORDINATE_NOTE,
            "heldout": {"available": False, "reason": "A temporary preview is not part of the frozen held-out evaluation.", "points": []},
            "curve": {"available": False, "reason": "A temporary preview is not a fitted-on-this-game estimate.", "points": []},
        }

    def frame_at(self, game_id: int, t: int) -> dict | None:
        import bisect

        built = _timeline(game_id)
        if built is None or not built["frames"]:
            return None
        index = bisect.bisect_right([frame["t"] for frame in built["frames"]], t) - 1
        index = max(index, 0)
        frame = built["frames"][index]
        return {
            "index": index,
            "squads": built["squads"],
            "checkpoints": {"heldout": None, "final": None},
            "requested_t": t,
            "read": {"source": "preview", "via": "temporary replay", "detail": "not stored in Tiger Data"},
            **frame,
        }

    def curve(self, game_id: int) -> dict:
        summary = self.summary(game_id)
        if summary is None:
            return {"available": False, "reason": "Preview not found.", "points": []}
        return summary["curve"]

    def heldout(self, game_id: int) -> dict:
        summary = self.summary(game_id)
        if summary is None:
            return {"available": False, "reason": "Preview not found.", "points": []}
        return summary["heldout"]
