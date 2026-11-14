"""Select Overstat games by per-game quality and replay validation.

Quality is selection metadata. It is not a model feature.
The displayed score for one game is ``games[].analytics.qualityScore`` on
``GET /api/stats/{sessionId}/overall``. The standings page passes that game
object when a game tab is selected, and the session-level
``analytics.qualityScore`` when the tab is Overall. This module never copies
the session score onto a game. The server computes the number; we do not.
"""

from __future__ import annotations

from typing import Any

from overstat_patch import (
    default_checkpoints,
    playing_bounds,
    replay,
    unsupported_deltas,
)

QUALITY_SCOPE = "individual_game"
QUALITY_SOURCE = (
    "GET https://overstat.gg/api/stats/{session_id}/overall "
    "field games[].analytics.qualityScore"
)
SESSION_QUALITY_SOURCE = (
    "GET https://overstat.gg/api/stats/{session_id}/overall "
    "field analytics.qualityScore"
)

# Explicit acceptance checks. These are not freshness thresholds.
# A Playing checkpoint counts as having positions when at least one player
# has a numeric pos.x and a non-null teamId.
# A winner is teamsAlive == 1 and exactly one team still not eliminated or dead.


def numeric_score(value: Any) -> float | None:
    """Return a real score. Missing and non-numeric values stay unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def quality_reason(score: float | None, minimum: float) -> str | None:
    if score is None:
        return "quality_unknown"
    if score < minimum:
        return "below_quality_threshold"
    return None


def game_quality(stats_game: dict | None) -> float | None:
    if not isinstance(stats_game, dict):
        return None
    analytics = stats_game.get("analytics")
    if not isinstance(analytics, dict) or "qualityScore" not in analytics:
        return None
    return numeric_score(analytics.get("qualityScore"))


def session_quality(stats: dict | None) -> float | None:
    if not isinstance(stats, dict):
        return None
    analytics = stats.get("analytics")
    if not isinstance(analytics, dict) or "qualityScore" not in analytics:
        return None
    return numeric_score(analytics.get("qualityScore"))


def mark_duplicates(records: list[dict]) -> None:
    """Mark later rows that repeat a game id, livedata id, or mid.

    The first row keeps its reason. A livedata id or mid that points at
    two game ids is recorded on the later row as an inconsistent mapping.
    """
    by_game: dict[Any, Any] = {}
    by_live: dict[str, Any] = {}
    by_mid: dict[str, Any] = {}
    for record in records:
        game_id = record.get("game_id")
        livedata = record.get("livedata_id") or None
        mid = record.get("mid") or None
        notes: list[str] = []
        if game_id in by_game:
            record["acceptance_status"] = "excluded"
            record["reason"] = "duplicate"
            record["duplicate_of"] = by_game[game_id]
            continue
        if livedata and livedata in by_live:
            record["acceptance_status"] = "excluded"
            record["reason"] = "duplicate"
            record["duplicate_of"] = by_live[livedata]
            if by_live[livedata] != game_id:
                notes.append(f"livedata {livedata} previously game {by_live[livedata]}")
        elif mid and mid in by_mid and by_mid[mid] != game_id:
            record["acceptance_status"] = "excluded"
            record["reason"] = "duplicate"
            record["duplicate_of"] = by_mid[mid]
            notes.append(f"mid {mid} previously game {by_mid[mid]}")
        if notes:
            record["inconsistent_mapping"] = "; ".join(notes)
        if record.get("reason") == "duplicate":
            continue
        by_game[game_id] = game_id
        if livedata:
            by_live[livedata] = game_id
        if mid:
            by_mid[mid] = game_id


def _coverage(rows: list[dict]) -> dict:
    with_x = 0
    with_team = 0
    fresh_x = 0
    carried_x = 0
    unknown_x = 0
    for row in rows:
        x = row["pos"]["value"]["x"]
        gap = row["pos"]["gap"]["x"]
        if x is None:
            unknown_x += 1
        else:
            with_x += 1
            if gap == 0:
                fresh_x += 1
            elif isinstance(gap, int) and gap > 0:
                carried_x += 1
        if row["teamId"]["value"] is not None or row["teamName"]["value"] is not None:
            with_team += 1
    return {
        "players": len(rows),
        "with_position_x": with_x,
        "with_team_identity": with_team,
        "fresh_pos_x": fresh_x,
        "carried_pos_x": carried_x,
        "unknown_pos_x": unknown_x,
    }


def _one_squad(groups: dict[Any, dict]) -> dict | None:
    if len(groups) != 1:
        return None
    squad = next(iter(groups.values()))
    if squad.get("teamId") is None or squad.get("name_conflict"):
        return None
    return {
        "teamId": squad["teamId"],
        "teamName": squad.get("teamName"),
        "players": list(squad.get("players") or []),
    }


def _add_player(groups: dict[Any, dict], team_id: Any, team_name: Any, player_name: Any) -> None:
    if team_id is None:
        return
    bucket = groups.setdefault(
        team_id,
        {"teamId": team_id, "teamName": None, "players": [], "name_conflict": False},
    )
    if team_name:
        if bucket["teamName"] and bucket["teamName"] != team_name:
            bucket["name_conflict"] = True
        elif bucket["teamName"] is None:
            bucket["teamName"] = team_name
    if player_name:
        bucket["players"].append(player_name)


def replay_placement_squad(state: dict) -> dict | None:
    """The replay's first-place squad, if exactly one teamId has teamPlacement 1."""
    groups: dict[Any, dict] = {}
    for player in (state.get("players") or {}).values():
        if not isinstance(player, dict) or player.get("teamPlacement") != 1:
            continue
        _add_player(groups, player.get("teamId"), player.get("teamName"), player.get("name"))
    return _one_squad(groups)


def standings_squad(stats_game: dict | None) -> dict | None:
    """Unique first place on this game's final standings. Not looked up by list order."""
    if not isinstance(stats_game, dict):
        return None
    groups: dict[Any, dict] = {}
    for team in stats_game.get("teams") or []:
        if not isinstance(team, dict):
            continue
        placement = (team.get("overall_stats") or {}).get("teamPlacement")
        if placement != 1:
            continue
        names = [
            player.get("name")
            for player in (team.get("player_stats") or [])
            if isinstance(player, dict) and player.get("name")
        ]
        _add_player(groups, team.get("teamId"), team.get("name"), None)
        if team.get("teamId") in groups:
            groups[team["teamId"]]["players"] = names
    return _one_squad(groups)


def identities_match(left: dict | None, right: dict | None) -> bool:
    if not left or not right:
        return False
    if left.get("teamId") is None or left.get("teamId") != right.get("teamId"):
        return False
    left_name = left.get("teamName") or ""
    right_name = right.get("teamName") or ""
    return not (left_name and right_name and left_name != right_name)


def winning_squad(state: dict) -> dict | None:
    if state.get("teamsAlive") != 1:
        return None
    teams: dict[Any, dict] = {}
    for player in (state.get("players") or {}).values():
        if not isinstance(player, dict):
            continue
        if player.get("status") in ("eliminated", "dead"):
            continue
        team_id = player.get("teamId")
        team_name = player.get("teamName")
        key = team_id if team_id is not None else team_name
        if key is None:
            continue
        bucket = teams.setdefault(
            key, {"teamId": team_id, "teamName": team_name, "players": []}
        )
        if player.get("name"):
            bucket["players"].append(player["name"])
    if len(teams) != 1:
        return None
    squad = next(iter(teams.values()))
    return {
        "teamId": squad["teamId"],
        "teamName": squad.get("teamName"),
        "players": list(squad.get("players") or []),
    }


def resolve_winner(state: dict, stats_game: dict | None = None) -> dict | None:
    """Label a winner only when the replay squad and this game's standings agree.

    Final placement is not copied onto player rows. The returned object is
    outcome metadata. ``stats_game`` must be the standings for this game id.
    """
    survivor = winning_squad(state)
    placed = replay_placement_squad(state)
    if survivor and placed and not identities_match(survivor, placed):
        return None
    replay_squad = survivor or placed
    if stats_game is None:
        if survivor is None:
            return None
        labeled = dict(survivor)
        labeled["label_source"] = "replay.teamsAlive"
        return labeled
    board = standings_squad(stats_game)
    if replay_squad is None or board is None or not identities_match(replay_squad, board):
        return None
    sources = []
    if survivor and identities_match(survivor, board):
        sources.append("replay.teamsAlive")
    if placed and identities_match(placed, board):
        sources.append("replay.player.teamPlacement")
    sources.append("stats.games[id].teams.overall_stats.teamPlacement")
    return {
        "teamId": replay_squad["teamId"],
        "teamName": replay_squad.get("teamName") or board.get("teamName"),
        "players": list(replay_squad.get("players") or []),
        "label_source": " + ".join(sources),
    }


def _has_position_and_team(rows: list[dict]) -> bool:
    for row in rows:
        if row["pos"]["value"]["x"] is not None and row["teamId"]["value"] is not None:
            return True
    return False


def validate_timeseries(payload: dict, stats_game: dict | None = None) -> dict:
    """Validate one timeseries. The raw series, including its last entry, is unchanged."""
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        return {"ok": False, "reason": "invalid_payload", "detail": "series array missing"}
    series = payload["series"]
    if not series:
        return {"ok": False, "reason": "invalid_payload", "detail": "series is empty"}
    bad: list[str] = []
    for entry in series:
        if isinstance(entry, dict) and entry.get("d") is not None:
            unsupported_deltas(entry["d"], f"t{entry.get('t')}", bad)
    if bad:
        return {
            "ok": False,
            "reason": "reconstruction_failed",
            "detail": "unsupported patch operations",
            "unsupported": bad,
        }
    try:
        probe = replay(series)
        bounds = playing_bounds(probe["state_names"])
    except Exception as exc:
        return {"ok": False, "reason": "reconstruction_failed", "detail": str(exc)}
    if probe["failures"]:
        return {
            "ok": False,
            "reason": "reconstruction_failed",
            "detail": probe["failures"][:5],
        }
    if bounds is None:
        return {"ok": False, "reason": "insufficient_position_data", "detail": "state never entered Playing"}
    checkpoints = default_checkpoints(probe["state_names"])
    replayed = replay(series, set(checkpoints))
    coverage = {
        str(t): _coverage(replayed["snapshots"].get(t, [])) for t in checkpoints
    }
    if not all(_has_position_and_team(replayed["snapshots"].get(t, [])) for t in checkpoints):
        return {
            "ok": False,
            "reason": "insufficient_position_data",
            "coverage": coverage,
            "playing_t": list(bounds),
        }
    winner = resolve_winner(replayed["state"], stats_game)
    last = series[-1]
    report = {
        "ok": winner is not None,
        "reason": "accepted" if winner is not None else "outcome_unknown",
        "playing_t": list(bounds),
        "checkpoints": coverage,
        "player_count_end": len(replayed["state"].get("players") or {}),
        "teams_alive_end": replayed["state"].get("teamsAlive"),
        "end_state": replayed["state_names"][-1] if replayed["state_names"] else None,
        "winner": winner,
        "timeline": {
            "raw_entries": len(series),
            "raw_last_t": last.get("t") if isinstance(last, dict) else None,
            "frontend_omitted_t": replayed["omitted_t"],
            "frontend_applied_frames": replayed["frame_count"],
            "raw_keeps_omitted_entry": isinstance(last, dict) and last.get("t") == replayed["omitted_t"],
        },
        "patch_failures": len(replayed["failures"]),
    }
    return report


def decide(record: dict, validation: dict | None, minimum: float) -> tuple[str, str]:
    """Return acceptance status and one reason. Quality never comes from the session score."""
    if record.get("reason") == "duplicate":
        return "excluded", "duplicate"
    if record.get("score_scope_unresolved"):
        return "excluded", "score_scope_unresolved"
    if not record.get("livedata_id"):
        return "excluded", "replay_missing"
    download_status = record.get("download_status")
    if download_status == "failed_temporary":
        return "excluded", "download_failed"
    if download_status == "unavailable":
        return "excluded", "replay_missing"
    qreason = quality_reason(record.get("quality_score"), minimum)
    if qreason:
        return "excluded", qreason
    if download_status in (None, "not_attempted"):
        return "pending", ""
    if validation is None:
        return "pending", "download_failed"
    if not validation.get("ok"):
        return "excluded", validation.get("reason") or "reconstruction_failed"
    return "accepted", "accepted"
