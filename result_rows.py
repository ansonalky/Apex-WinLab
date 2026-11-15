"""Per-game team and player results from cached Overstat standings.

Source, for each accepted game, is
``data/collection/cache/stats-{session_id}.json``, the game whose ``id``
equals the manifest ``game_id``. Fields are that game's
``teams[].overall_stats`` and ``teams[].player_stats``.

``teamId`` in a replay or in these standings is match-local. Rows are not
grouped by it. A roster is the sorted set of Overstat ``playerId`` values
recorded on that team in that game. Missing stats stay None.
"""

from __future__ import annotations

import json
from pathlib import Path

# A game is compatible with this scoring scheme when every team's recorded
# score equals these placement points plus one per kill. Matching recorded
# scores does not establish an event's official rules, so the label says
# "compatible", not "verified" or "official".
PLACEMENT_POINTS = {1: 12, 2: 9, 3: 7, 4: 5, 5: 4, 6: 3, 7: 3, 8: 2, 9: 2, 10: 2, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1}
SCORING_COMPATIBLE = "placement_plus_kill_compatible"
SCORING_NOT_COMPATIBLE = "not_compatible"


def whole(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def placement_points(place: int | None) -> int | None:
    if place is None or place < 1:
        return None
    return PLACEMENT_POINTS.get(place, 0)


def squad_format(player_counts: list[int]) -> str:
    if not player_counts:
        return "unverified"
    largest = max(player_counts)
    if largest == 3:
        return "trios"
    if largest == 2:
        return "duos"
    if largest == 1:
        return "solos"
    return "unverified"


def roster_key(player_ids: list[int], game_id: int, team_id: int | None) -> str:
    if player_ids:
        return ",".join(str(player_id) for player_id in sorted(player_ids))
    return f"unresolved:{game_id}:{team_id if team_id is not None else 'none'}"


def _accepted(data_dir: Path) -> list[dict]:
    manifest = json.loads((data_dir / "collection" / "manifest.json").read_text(encoding="utf-8"))
    return [row for row in manifest["games"].values() if row.get("reason") == "accepted"]


def _stats(data_dir: Path, session_id: int, cache: dict) -> dict:
    if session_id not in cache:
        path = data_dir / "collection" / "cache" / f"stats-{session_id}.json"
        cache[session_id] = json.loads(path.read_text(encoding="utf-8"))
    return cache[session_id]


def _game(document: dict, game_id: int) -> dict | None:
    for game in document.get("games") or []:
        if game.get("id") == game_id:
            return game
    return None


def game_is_comparable(teams: list[dict]) -> bool:
    if not teams:
        return False
    for team in teams:
        overall = team.get("overall_stats") or {}
        place = whole(overall.get("teamPlacement"))
        kills = whole(overall.get("kills"))
        score = whole(overall.get("score"))
        points = placement_points(place)
        if place is None or kills is None or score is None or points is None:
            return False
        if score != points + kills:
            return False
    return True


def load_results(data_dir: Path) -> tuple[list[dict], list[dict]]:
    """One team row and one player row per recorded result. No frame sums."""
    data_dir = data_dir.resolve()
    teams_out: list[dict] = []
    players_out: list[dict] = []
    cache: dict = {}
    for record in _accepted(data_dir):
        document = _stats(data_dir, record["session_id"], cache)
        game = _game(document, record["game_id"])
        if game is None:
            raise ValueError(f"standings have no game {record['game_id']}")
        listed = [team for team in (game.get("teams") or []) if isinstance(team, dict)]
        counts = [len(team.get("player_stats") or []) for team in listed]
        form = squad_format(counts)
        comparable = game_is_comparable(listed)
        scoring = SCORING_COMPATIBLE if comparable else SCORING_NOT_COMPATIBLE
        seen_rosters: set[str] = set()
        seen_players: set[int] = set()
        for team in listed:
            overall = team.get("overall_stats") or {}
            player_stats = [player for player in (team.get("player_stats") or []) if isinstance(player, dict)]
            player_ids = [whole(player.get("playerId")) for player in player_stats]
            if any(player_id is None for player_id in player_ids):
                raise ValueError(f"game {record['game_id']} has a player without playerId")
            key = roster_key(player_ids, record["game_id"], whole(team.get("teamId")))
            if key in seen_rosters:
                raise ValueError(f"game {record['game_id']} repeats roster {key}")
            seen_rosters.add(key)
            teams_out.append(
                {
                    "game_id": record["game_id"],
                    "session_id": record["session_id"],
                    "event": record.get("event"),
                    "map_name": record.get("map_name"),
                    "roster_key": key,
                    "team_id": whole(team.get("teamId")),
                    "team_name": team.get("name") or overall.get("name") or "",
                    "squad_format": form,
                    "scoring_format": scoring,
                    "points_comparable": comparable,
                    "placement": whole(overall.get("teamPlacement")),
                    "kills": whole(overall.get("kills")),
                    "assists": whole(overall.get("assists")),
                    "damage": whole(overall.get("damageDealt")),
                    "score": whole(overall.get("score")),
                    "player_count": len(player_stats),
                }
            )
            for player in player_stats:
                player_id = whole(player.get("playerId"))
                if player_id in seen_players:
                    raise ValueError(f"game {record['game_id']} repeats player {player_id}")
                seen_players.add(player_id)
                players_out.append(
                    {
                        "game_id": record["game_id"],
                        "session_id": record["session_id"],
                        "event": record.get("event"),
                        "map_name": record.get("map_name"),
                        "player_id": player_id,
                        "player_name": player.get("name") or "",
                        "team_name": team.get("name") or "",
                        "roster_key": key,
                        "squad_format": form,
                        "kills": whole(player.get("kills")),
                        "assists": whole(player.get("assists")),
                        "damage": whole(player.get("damageDealt")),
                    }
                )
    return teams_out, players_out
