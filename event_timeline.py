"""Map event timeline. Display only.

Knocks, confirmed kills, and squad eliminations stay separate labels.
Nothing here is read by the frozen model or the coach packet. A ring end
that was not recorded stays missing; it is not filled with the last replay
second or the t=0 plan.
"""

from __future__ import annotations

from typing import Any, Callable

from features import elimination_team
from ring_features import GEOMETRY_KEYS


def _person(value: Any) -> tuple[Any, str | None]:
    if not isinstance(value, dict):
        return None, None
    return value.get("teamId"), value.get("name") if isinstance(value.get("name"), str) else None


def ring_span(entry: dict) -> dict:
    """One live ring entry. ``end_t`` is set only when that entry records an end."""
    start = entry.get("start")
    end = entry.get("end")
    start_ok = isinstance(start, (int, float)) and not isinstance(start, bool)
    end_ok = (
        isinstance(end, (int, float))
        and not isinstance(end, bool)
        and all(key in entry for key in GEOMETRY_KEYS)
    )
    return {
        "stage": entry.get("stage"),
        "start_t": int(start) if start_ok else None,
        "end_t": int(end) if end_ok else None,
        "end_recorded": bool(end_ok),
    }


def display_feed(series: list[dict]) -> list[dict]:
    """Replay-clock feed rows for the timeline.

    A derived ``down`` is skipped: it is inferred beside a kill and is not a
    separate confirmed knock. A ``kill`` is confirmed only when ``credit`` is
    true. ``eliminated`` stays a squad elimination, not a player kill.
    """
    rows = []
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
            kind = event.get("type")
            actor_team, actor_name = _person(event.get("player"))
            victim_team, victim_name = _person(event.get("victim"))
            weapon = event.get("weapon") if isinstance(event.get("weapon"), str) else None
            if kind == "down":
                if event.get("derived") is True:
                    continue
                rows.append({
                    "t": t, "type": "knock", "confirmed": True,
                    "actor_team_id": actor_team, "actor_name": actor_name,
                    "victim_team_id": victim_team, "victim_name": victim_name,
                    "weapon": weapon,
                })
            elif kind == "kill":
                rows.append({
                    "t": t, "type": "kill", "confirmed": event.get("credit") is True,
                    "actor_team_id": actor_team, "actor_name": actor_name,
                    "victim_team_id": victim_team, "victim_name": victim_name,
                    "weapon": weapon,
                })
            elif kind == "eliminated":
                team, problem = elimination_team(event)
                rows.append({
                    "t": t, "type": "squad_elimination", "confirmed": problem is None,
                    "actor_team_id": None, "actor_name": None,
                    "victim_team_id": team, "victim_name": None,
                    "weapon": None, "ambiguous": problem is not None,
                })
    return rows


def _same(team: Any, other: Any) -> bool:
    return other is not None and str(other) == str(team)


def markers_for_team(feed: list[dict], team: Any, elapsed_of: Callable[[int], int | None]) -> list[dict]:
    """Markers for the selected squad. Kills, knocks, deaths, and squad wipes stay distinct."""
    markers = []
    for event in feed:
        actor = event.get("actor_team_id")
        victim = event.get("victim_team_id")
        kind = None
        if event["type"] == "knock" and (_same(team, actor) or _same(team, victim)):
            kind = "knock"
        elif event["type"] == "kill" and event.get("confirmed") and _same(team, actor) and not _same(team, victim):
            kind = "kill"
        elif event["type"] == "kill" and _same(team, victim):
            kind = "death"
        elif event["type"] == "squad_elimination" and event.get("confirmed") and _same(team, victim):
            kind = "squad_elimination"
        if kind is None:
            continue
        actor_name = event.get("actor_name") or "Unknown"
        victim_name = event.get("victim_name") or "Unknown"
        weapon = event.get("weapon")
        if kind == "knock":
            text = f"Knock · {actor_name} downed {victim_name}"
        elif kind == "kill":
            text = f"Confirmed kill · {actor_name} eliminated {victim_name}"
        elif kind == "death" and event.get("confirmed"):
            text = f"Death · {victim_name} · confirmed kill by {actor_name}"
        elif kind == "death":
            cause = weapon or "uncredited"
            text = f"Death · {victim_name} · {cause} · not a confirmed kill"
        else:
            text = "Squad eliminated"
        markers.append({
            "t": event["t"],
            "elapsed": elapsed_of(event["t"]),
            "kind": kind,
            "confirmed": bool(event.get("confirmed")),
            "text": text,
        })
    return markers
