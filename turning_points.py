"""Largest changes in a squad's per-second win estimate.

The input is one prediction kind and one model version, one probability per
replay second. Missing seconds are not filled in. The recorded outcome
(100% after winner determination) is not a prediction and is not used.

This module does not fit a model and does not change the frozen evaluation.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from features import elimination_team
from live_predictions import FINAL, HELD_OUT, LivePredictions

LABEL = "Largest changes in model estimate."
DEFAULT_WINDOWS = (5, 10, 20)

_EVENT_TYPES = {
    "down": "knock",
    "revive": "revive",
    "eliminated": "squad_elimination",
    "respawn": "respawn",
    "damage": "damage",
}


def predictions_from_table(table: dict[int, dict], *, kind: str, provenance: dict) -> list[dict]:
    """Rows for ``detect_turning_points`` from ``LivePredictions.table``.

    ``model`` is the eligibility-constrained probability. Unconstrained
    output is not mixed in.
    """
    rows = []
    for t in sorted(table):
        point = table[t]
        for squad in point["squads"]:
            rows.append(
                {
                    "t": point["t"],
                    "elapsed": point["elapsed"],
                    "team_id": squad["teamId"],
                    "team_name": squad.get("team_name"),
                    "probability": squad["model"],
                    "kind": kind,
                    "provenance": provenance,
                }
            )
    return rows


def _team_id(player: Any) -> Any:
    if isinstance(player, dict) and "teamId" in player:
        return player.get("teamId")
    return None


def _name(player: Any) -> str | None:
    if isinstance(player, dict) and isinstance(player.get("name"), str):
        return player["name"]
    return None


def recorded_events(series: list[dict], playing_start: int | None = None) -> list[dict]:
    """Feed events on the replay clock of the frame that carried them.

    The frame ``t`` is the verified replay second. The event's own unix
    timestamp is not used. Player ``kill`` events are omitted; a squad wipe
    is the ``eliminated`` event.
    """
    events = []
    for frame in series:
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), int):
            continue
        t = frame["t"]
        feed = frame.get("f") or []
        if not isinstance(feed, list):
            continue
        for event in feed:
            if not isinstance(event, dict):
                continue
            kind = _EVENT_TYPES.get(event.get("type"))
            if kind is None:
                continue
            actor = event.get("player") if isinstance(event.get("player"), dict) else None
            victim = event.get("victim") if isinstance(event.get("victim"), dict) else None
            actor_team = _team_id(actor)
            victim_team = _team_id(victim)
            ambiguous = False
            if kind == "squad_elimination":
                team, problem = elimination_team(event)
                ambiguous = problem is not None
                victim_team = team
                actor_team = None
            events.append(
                {
                    "t": t,
                    "elapsed": None if playing_start is None else t - playing_start,
                    "type": kind,
                    "actor_team_id": actor_team,
                    "actor_name": _name(actor),
                    "victim_team_id": victim_team,
                    "victim_name": _name(victim),
                    "ambiguous": ambiguous,
                }
            )
    return events


def _side(event: dict, squad_id: Any) -> str:
    if event.get("ambiguous"):
        return "unattributed"
    involved = {event.get("actor_team_id"), event.get("victim_team_id")}
    involved.discard(None)
    if not involved:
        return "unattributed"
    if squad_id in involved:
        return "squad"
    return "opponent"


def _version_key(row: dict) -> tuple:
    provenance = row.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {"value": provenance}
    identity = provenance.get("sha256") or json.dumps(provenance, sort_keys=True, default=str)
    return (row.get("kind"), identity)


def _usable(row: dict, conclusion_t: int | None) -> bool:
    if row.get("recorded_outcome"):
        return False
    if conclusion_t is not None and row["t"] >= conclusion_t:
        return False
    return isinstance(row.get("probability"), (int, float)) and not isinstance(row["probability"], bool)


def _phrase(events: list[dict]) -> str:
    counts = Counter(event["type"] for event in events)
    labels = {
        "knock": "knocks",
        "revive": "revives",
        "squad_elimination": "squad eliminations",
        "respawn": "respawns",
    }
    return ", ".join(f"{counts[key]} {labels[key]}" for key in labels if counts[key])


def _describe(squad_id: Any, listed: list[dict], damage: dict) -> str:
    parts = []
    squad_events = [event for event in listed if event["side"] == "squad"]
    opponent_events = [event for event in listed if event["side"] == "opponent"]
    other_events = [event for event in listed if event["side"] == "unattributed"]
    if squad_events:
        parts.append(f"Selected squad: {_phrase(squad_events)}.")
    if opponent_events:
        parts.append(f"Other squads: {_phrase(opponent_events)}.")
    if other_events:
        parts.append(f"Unattributed: {_phrase(other_events)}.")
    if damage["packets"]:
        parts.append(
            "Damage packets: "
            f"{damage['by_squad']} by the selected squad, "
            f"{damage['against_squad']} against the selected squad, "
            f"{damage['other']} between other squads."
        )
    if any(event["type"] == "squad_elimination" and event["side"] == "opponent" for event in listed):
        parts.append(
            "An opponent elimination can move this estimate with no recorded action by the selected squad."
        )
    if not parts:
        return "No knocks, revives, damage, squad eliminations, or respawns were recorded in this interval."
    return "Coincident recorded events only. " + " ".join(parts)


def _attach(highlight: dict, events: list[dict], squad_id: Any) -> dict:
    windowed = [event for event in events if highlight["start_t"] <= event["t"] <= highlight["end_t"]]
    listed = []
    damage = {"packets": 0, "by_squad": 0, "against_squad": 0, "other": 0}
    for event in windowed:
        side = _side(event, squad_id)
        if event["type"] == "damage":
            damage["packets"] += 1
            actor = event.get("actor_team_id")
            victim = event.get("victim_team_id")
            if actor == squad_id:
                damage["by_squad"] += 1
            elif victim == squad_id:
                damage["against_squad"] += 1
            else:
                damage["other"] += 1
            continue
        listed.append({**event, "side": side})
    highlight["events"] = listed
    highlight["damage"] = damage
    highlight["description"] = _describe(squad_id, listed, damage)
    return highlight


def detect_turning_points(
    predictions: list[dict],
    events: list[dict] | None = None,
    *,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    min_change_pp: float = 5.0,
    limit: int = 3,
    conclusion_t: int | None = None,
    game_id: Any = None,
    squad_id: Any = None,
) -> dict:
    """Largest upward and downward estimate changes, in percentage points.

    A window is used only when every replay second from its start through
    its end has a prediction for that squad. Overlapping windows of the
    same squad and direction become one highlight. The highlight keeps the
    window with the largest absolute change; equal changes keep the
    shorter window.
    """
    rows = [row for row in predictions if squad_id is None or row.get("team_id") == squad_id]
    rows = [row for row in rows if _usable(row, conclusion_t)]
    if rows:
        keys = {_version_key(row) for row in rows}
        if len(keys) != 1 or None in {row.get("kind") for row in rows}:
            raise ValueError("predictions must share one kind and one model version")
    by_team: dict[Any, dict[int, dict]] = {}
    names: dict[Any, Any] = {}
    provenance = rows[0]["provenance"] if rows else None
    kind = rows[0]["kind"] if rows else None
    for row in rows:
        team = row.get("team_id")
        slot = by_team.setdefault(team, {})
        if row["t"] in slot:
            raise ValueError(f"duplicate prediction for squad {team} at t={row['t']}")
        slot[row["t"]] = row
        if row.get("team_name"):
            names[team] = row["team_name"]
    raw: list[dict] = []
    for team, points in by_team.items():
        for start, row in points.items():
            for window in windows:
                if window <= 0:
                    raise ValueError("windows must be positive")
                end = start + window
                if any(second not in points for second in range(start, end + 1)):
                    continue
                before = float(row["probability"])
                after = float(points[end]["probability"])
                change = round((after - before) * 100, 6)
                if abs(change) < min_change_pp or change == 0:
                    continue
                raw.append(
                    {
                        "team_id": team,
                        "team_name": names.get(team),
                        "direction": "up" if change > 0 else "down",
                        "window_s": window,
                        "start_t": start,
                        "end_t": end,
                        "start_elapsed": row.get("elapsed"),
                        "end_elapsed": points[end].get("elapsed"),
                        "before": before,
                        "after": after,
                        "change_pp": change,
                    }
                )
    highlights = []
    for team in by_team:
        for direction in ("up", "down"):
            chosen = [item for item in raw if item["team_id"] == team and item["direction"] == direction]
            chosen.sort(key=lambda item: (item["start_t"], item["end_t"]))
            groups: list[dict] = []
            for item in chosen:
                if groups and item["start_t"] <= groups[-1]["end"]:
                    groups[-1]["items"].append(item)
                    groups[-1]["end"] = max(groups[-1]["end"], item["end_t"])
                else:
                    groups.append({"end": item["end_t"], "items": [item]})
            for group in groups:
                members = group["items"]
                best = max(members, key=lambda item: (abs(item["change_pp"]), -item["window_s"], -item["start_t"]))
                # Elapsed time is seconds since Playing start, so the episode
                # uses the same origin as the representative window.
                origin = None
                start_elapsed = best.get("start_elapsed")
                if isinstance(start_elapsed, int) and not isinstance(start_elapsed, bool):
                    origin = best["start_t"] - start_elapsed
                episode_start_t = members[0]["start_t"]
                episode_end_t = group["end"]
                highlight = {
                    "label": LABEL,
                    "game_id": game_id,
                    "squad_id": best["team_id"],
                    "squad_name": best["team_name"],
                    "direction": best["direction"],
                    "window_s": best["window_s"],
                    "start_t": best["start_t"],
                    "end_t": best["end_t"],
                    "start_elapsed": best["start_elapsed"],
                    "end_elapsed": best["end_elapsed"],
                    "before": best["before"],
                    "after": best["after"],
                    "change_pp": best["change_pp"],
                    "prediction_kind": kind,
                    "provenance": provenance,
                    "windows_merged": len(members),
                    # The merged episode can be longer than the reported window.
                    "episode_start_t": episode_start_t,
                    "episode_end_t": episode_end_t,
                    "episode_start_elapsed": None if origin is None else episode_start_t - origin,
                    "episode_end_elapsed": None if origin is None else episode_end_t - origin,
                    "seek": {"game_id": game_id, "t": best["start_t"], "elapsed": best["start_elapsed"]},
                }
                highlights.append(_attach(highlight, events or [], best["team_id"]))
    up = sorted((item for item in highlights if item["direction"] == "up"), key=lambda item: -item["change_pp"])[:limit]
    down = sorted((item for item in highlights if item["direction"] == "down"), key=lambda item: item["change_pp"])[:limit]
    return {"label": LABEL, "game_id": game_id, "kind": kind, "up": up, "down": down}


def _missing(live: LivePredictions, game_id: Any, kind: str) -> list[str]:
    from dashboard_data import EXPANSION_REASON, _record, frozen_model_applies

    record = _record(live.data_dir, game_id)
    if record is not None and not frozen_model_applies(live.data_dir, record):
        return [EXPANSION_REASON]
    if kind == HELD_OUT:
        if not live.bundle.get("available"):
            reason = live.bundle.get("reason") or "held-out bundle is not available"
            return [reason]
        if game_id not in live.test_games:
            return [f"game {game_id} is not in the held-out test split"]
        return ["exports/heldout-session-split-v2/evaluation_model.json"]
    if kind == FINAL:
        return ["models/model.json"]
    return [f"unknown prediction kind {kind}"]


def canonical_squad(squad_id: Any) -> Any:
    """Match query-string squad ids to numeric ``teamId`` values."""
    if squad_id is None or isinstance(squad_id, bool):
        return squad_id
    if isinstance(squad_id, int):
        return squad_id
    text = str(squad_id).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def model_version(live: LivePredictions, kind: str) -> str | None:
    """The frozen file's sha256, or None when that model is not loaded."""
    if kind not in (HELD_OUT, FINAL):
        return None
    model = live.model(kind)
    if not model:
        return None
    provenance = model.get("provenance") or {}
    if not isinstance(provenance, dict):
        return json.dumps(provenance, sort_keys=True, default=str)
    return provenance.get("sha256") or json.dumps(provenance, sort_keys=True, default=str)


def highlights_for_game(
    data_dir: Path,
    game_id: Any,
    kind: str,
    squad_id: Any = None,
    *,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    min_change_pp: float = 5.0,
    limit: int = 3,
    conclusion_t: int | None = None,
    live: LivePredictions | None = None,
) -> dict:
    """Per-second highlights for one game, or the missing artifact.

    Does not invent probabilities when ``LivePredictions`` cannot build a table.
    Pass the dashboard's ``live`` to reuse its cached prediction tables.
    """
    data_dir = Path(data_dir)
    squad_id = canonical_squad(squad_id)
    live = live or LivePredictions(data_dir)
    if kind not in (HELD_OUT, FINAL) or not live.available(game_id, kind):
        return {
            "available": False,
            "label": LABEL,
            "game_id": game_id,
            "kind": kind,
            "missing": _missing(live, game_id, kind),
            "up": [],
            "down": [],
        }
    table = live.table(game_id, kind)
    provenance = live.model(kind)["provenance"]
    probe = live.at(game_id, -1, kind)
    cutoff = probe.get("cutoff_t")
    stop = cutoff if conclusion_t is None else conclusion_t
    from dashboard_data import _raw_path, _record

    record = _record(data_dir, game_id)
    path = _raw_path(record, data_dir) if record else None
    feed = []
    playing_start = None
    if path is not None and path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        series = payload.get("series") or []
        playing_start = None
        if table:
            first = table[min(table)]
            playing_start = first["t"] - first["elapsed"]
        feed = recorded_events(series, playing_start)
    found = detect_turning_points(
        predictions_from_table(table, kind=kind, provenance=provenance),
        feed,
        windows=windows,
        min_change_pp=min_change_pp,
        limit=limit,
        conclusion_t=stop,
        game_id=game_id,
        squad_id=squad_id,
    )
    found["available"] = True
    found["cutoff_t"] = cutoff
    found["missing"] = []
    if found.get("kind") is None:
        found["kind"] = kind
    found["provenance"] = provenance
    return found


class HighlightCache:
    """Highlight lists keyed by game, prediction kind, squad, and model version.

    The version is the frozen model's sha256. A different file does not reuse
    an older list. ``live`` is the dashboard's ``LivePredictions``, so the
    per-second tables are not built a second way.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple, dict] = {}

    def get(
        self,
        data_dir: Path,
        game_id: Any,
        kind: str,
        squad_id: Any,
        *,
        live: LivePredictions,
        **kwargs,
    ) -> dict:
        squad = canonical_squad(squad_id)
        version = model_version(live, kind)
        key = (str(game_id), str(kind), squad, version)
        with self._lock:
            cached = self._values.get(key)
        if cached is not None:
            return cached
        result = highlights_for_game(data_dir, game_id, kind, squad, live=live, **kwargs)
        with self._lock:
            return self._values.setdefault(key, result)
