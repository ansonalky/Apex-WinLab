"""Small synthetic replays for import and inference tests. No network."""

from __future__ import annotations

import json


def eliminated_event(team: int, size: int = 2) -> dict:
    return {"type": "eliminated", "timestamp": "0", "team": [{"teamId": team, "name": f"p{team}{i}"} for i in range(size)]}


def series(*, winner: int = 1, teams: int = 3, playing_at: int = 4, out_at: int = 8, decided_at: int = 12,
           eliminate: int | None = None) -> dict:
    """Playing, one optional squad elimination, then a decided ending.

    The last entry is past the decision so the frontend timeline includes the
    WinnerDetermined second. That second is the prediction cut-off.
    """
    players = {}
    for team in range(1, teams + 1):
        for slot in range(2):
            players[f"t{team}s{slot}"] = {
                "name": f"p{team}{slot}",
                "teamId": team,
                "teamName": f"T{team}",
                "pos": {"x": team * 100.0 + slot, "y": team * 10.0, "z": 0},
                "status": "alive",
                "currentHealth": 100,
                "maxHealth": 100,
                "shieldHealth": 50,
                "shieldMaxHealth": 50,
            }
    entries = {
        0: {"state": ["Prematch"], "teamsAlive": [teams], "totalTeams": [teams], "players": [players]},
        playing_at: {"state": ["Prematch", "Playing"]},
    }
    if eliminate is not None:
        losers = {
            f"t{eliminate}s{slot}": {"status": ["alive", "eliminated"]}
            for slot in range(2)
        }
        entries[out_at] = {"players": losers, "teamsAlive": [teams, teams - 1]}
    placement = {
        f"t{team}s{slot}": {"teamPlacement": [1 if team == winner else 2]}
        for team in range(1, teams + 1)
        for slot in range(2)
    }
    decided = entries.setdefault(decided_at, {})
    alive_before = teams - 1 if eliminate is not None else teams
    decided["teamsAlive"] = [alive_before, 1]
    for key, value in placement.items():
        decided.setdefault("players", {}).setdefault(key, {}).update(value)
    entries[decided_at + 2] = {"state": ["Playing", "WinnerDetermined"]}
    entries[decided_at + 3] = {"state": ["WinnerDetermined", "Postmatch"]}
    payload = {"series": [{"t": t, "d": d, "f": []} for t, d in sorted(entries.items())]}
    if eliminate is not None:
        for entry in payload["series"]:
            if entry["t"] == out_at:
                entry["f"] = [eliminated_event(eliminate)]
    return payload


def standings(game_id: int, winner: int = 1, teams: int = 3) -> dict:
    return {
        "id": game_id,
        "teams": [
            {
                "teamId": team,
                "name": f"T{team}",
                "overall_stats": {"teamPlacement": 1 if team == winner else team},
            }
            for team in range(1, teams + 1)
        ],
    }


def without_winner(payload: dict, decided_at: int = 12) -> dict:
    """Drop the decided frames so the replay stays playable and has no winner."""
    kept = [entry for entry in payload["series"] if entry["t"] < decided_at]
    # ``{"_t": "a"}`` is an empty object delta. An empty dict is not a valid patch.
    kept.append({"t": decided_at, "d": {"_t": "a"}, "f": []})
    return {"series": kept}


def tagged(payload: dict, marker: str) -> bytes:
    """Same replay, distinct file bytes, so duplicate detection stays per replay."""
    return json.dumps(dict(payload) | {"fixture": marker}).encode()
