"""Tiger Data queries for the performance page.

Aggregates read ``team_results`` and ``player_results`` joined to ``matches``.
They do not join ``player_frames``, so a frame cannot multiply a result row.
"""

from __future__ import annotations

TEAM_LEADERBOARD = """
WITH scoped AS (
  SELECT tr.roster_key, tr.team_name, tr.team_id, tr.placement, tr.kills,
         tr.damage, tr.assists, tr.score, tr.points_comparable, tr.scoring_format,
         tr.squad_format, tr.player_count, tr.game_id,
         m.session_id, m.event, m.map_name
  FROM team_results tr
  JOIN matches m ON m.game_id = tr.game_id
  WHERE (%(squad_format)s::text IS NULL OR tr.squad_format = %(squad_format)s)
    AND (%(map_name)s::text IS NULL OR m.map_name = %(map_name)s)
    AND (%(session_id)s::integer IS NULL OR m.session_id = %(session_id)s)
    AND (
      %(scoring)s = 'all'
      OR (%(scoring)s = 'compatible' AND tr.points_comparable)
      OR (%(scoring)s = 'not_compatible' AND NOT tr.points_comparable)
    )
),
shared_names AS (
  SELECT team_name
  FROM scoped
  GROUP BY team_name
  HAVING COUNT(DISTINCT roster_key) > 1
),
name_pick AS (
  SELECT roster_key, team_name
  FROM (
    SELECT roster_key, team_name, COUNT(*) AS n,
           ROW_NUMBER() OVER (
             PARTITION BY roster_key
             ORDER BY COUNT(*) DESC, team_name
           ) AS rk
    FROM scoped
    GROUP BY roster_key, team_name
  ) ranked
  WHERE rk = 1
),
agg AS (
  SELECT s.roster_key,
         COUNT(*)::integer AS games,
         COUNT(DISTINCT s.session_id)::integer AS sessions,
         AVG(s.placement) AS avg_placement,
         AVG(s.kills) AS kills_per_game,
         AVG(s.damage) AS damage_per_game,
         AVG(s.score) AS avg_score,
         AVG((s.placement = 1)::int) AS win_rate,
         AVG((s.placement <= 5)::int) AS top5_rate,
         BOOL_AND(s.points_comparable) AS all_comparable,
         COUNT(DISTINCT s.scoring_format)::integer AS scoring_formats,
         COUNT(DISTINCT s.team_name)::integer AS name_count,
         MIN(s.player_count)::integer AS min_players,
         MAX(s.player_count)::integer AS max_players
  FROM scoped s
  GROUP BY s.roster_key
  HAVING COUNT(*) >= %(min_games)s
)
SELECT a.roster_key, n.team_name, a.games, a.sessions,
       a.avg_placement, a.kills_per_game, a.damage_per_game,
       CASE WHEN %(allow_points)s AND a.all_comparable AND a.scoring_formats = 1
            THEN a.avg_score END AS avg_points,
       a.win_rate, a.top5_rate, a.name_count, a.min_players, a.max_players,
       EXISTS (
         SELECT 1 FROM shared_names s WHERE s.team_name = n.team_name
       ) AS shared_name
FROM agg a
JOIN name_pick n ON n.roster_key = a.roster_key
ORDER BY
  CASE WHEN %(rank_by)s = 'avg_points' THEN
    CASE WHEN %(allow_points)s AND a.all_comparable AND a.scoring_formats = 1
         THEN a.avg_score END
  END DESC NULLS LAST,
  CASE WHEN %(rank_by)s = 'avg_placement' THEN a.avg_placement END ASC NULLS LAST,
  a.games DESC,
  n.team_name
"""

TEAM_DETAIL = """
SELECT tr.game_id, tr.team_id, tr.team_name, tr.placement, tr.kills, tr.damage,
       tr.assists, tr.score, tr.points_comparable, tr.player_count,
       m.session_id, m.event, m.map_name
FROM team_results tr
JOIN matches m ON m.game_id = tr.game_id
WHERE tr.roster_key = %(roster_key)s
  AND (%(squad_format)s::text IS NULL OR tr.squad_format = %(squad_format)s)
  AND (%(map_name)s::text IS NULL OR m.map_name = %(map_name)s)
  AND (%(session_id)s::integer IS NULL OR m.session_id = %(session_id)s)
  AND (
    %(scoring)s = 'all'
    OR (%(scoring)s = 'compatible' AND tr.points_comparable)
    OR (%(scoring)s = 'not_compatible' AND NOT tr.points_comparable)
  )
ORDER BY m.session_id, tr.game_id
"""

PLAYER_LEADERBOARD = """
WITH scoped AS (
  SELECT pr.player_id, pr.player_name, pr.team_name, pr.kills, pr.damage,
         pr.assists, pr.game_id, m.session_id
  FROM player_results pr
  JOIN matches m ON m.game_id = pr.game_id
  JOIN team_results tr ON tr.game_id = pr.game_id AND tr.roster_key = pr.roster_key
  WHERE (%(squad_format)s::text IS NULL OR pr.squad_format = %(squad_format)s)
    AND (%(map_name)s::text IS NULL OR m.map_name = %(map_name)s)
    AND (%(session_id)s::integer IS NULL OR m.session_id = %(session_id)s)
    AND (
      %(scoring)s = 'all'
      OR (%(scoring)s = 'compatible' AND tr.points_comparable)
      OR (%(scoring)s = 'not_compatible' AND NOT tr.points_comparable)
    )
)
SELECT player_id,
       (ARRAY_AGG(player_name ORDER BY game_id DESC))[1] AS player_name,
       COUNT(*)::integer AS games,
       COUNT(DISTINCT session_id)::integer AS sessions,
       AVG(kills) AS kills_per_game,
       AVG(damage) AS damage_per_game,
       AVG(assists) AS assists_per_game,
       ARRAY_AGG(DISTINCT team_name ORDER BY team_name) AS team_names
FROM scoped
GROUP BY player_id
HAVING COUNT(*) >= %(min_games)s
ORDER BY games DESC, player_name
"""

PLAYER_DETAIL = """
SELECT pr.game_id, pr.player_name, pr.team_name, pr.kills, pr.damage, pr.assists,
       m.session_id, m.event, m.map_name, pr.roster_key
FROM player_results pr
JOIN matches m ON m.game_id = pr.game_id
JOIN team_results tr ON tr.game_id = pr.game_id AND tr.roster_key = pr.roster_key
WHERE pr.player_id = %(player_id)s
  AND (%(squad_format)s::text IS NULL OR pr.squad_format = %(squad_format)s)
  AND (%(map_name)s::text IS NULL OR m.map_name = %(map_name)s)
  AND (%(session_id)s::integer IS NULL OR m.session_id = %(session_id)s)
  AND (
    %(scoring)s = 'all'
    OR (%(scoring)s = 'compatible' AND tr.points_comparable)
    OR (%(scoring)s = 'not_compatible' AND NOT tr.points_comparable)
  )
ORDER BY m.session_id, pr.game_id
"""

OPTIONS = """
SELECT m.map_name, m.session_id, m.event, tr.squad_format
FROM team_results tr
JOIN matches m ON m.game_id = tr.game_id
"""


def _num(value):
    if value is None:
        return None
    return float(value)


def scope_params(filters: dict, *, allow_points: bool, rank_by: str) -> dict:
    form = filters.get("squad_format")
    if form in (None, "", "all"):
        form = None
    session = filters.get("session_id")
    if session in (None, "", "all"):
        session = None
    else:
        session = int(session)
    scoring = filters.get("scoring") or "compatible"
    if scoring not in ("compatible", "not_compatible", "all"):
        scoring = "compatible"
    return {
        "squad_format": form,
        "map_name": filters.get("map_name") or None,
        "session_id": session,
        "scoring": scoring,
        "min_games": int(filters.get("min_games", 5)),
        "allow_points": allow_points,
        "rank_by": rank_by,
    }


def ranking(filters: dict) -> tuple[bool, str, str]:
    """Whether average points may rank this filter, and the note to show."""
    form = filters.get("squad_format") or "trios"
    scoring = filters.get("scoring") or "compatible"
    if form in ("all", "", None):
        return False, "avg_placement", (
            "Squad formats are mixed, so teams are ranked by average placement. "
            "Average points are not combined across formats."
        )
    if scoring == "compatible":
        return True, "avg_points", (
            "Ranked by average recorded score within this squad format. "
            "Every included game is compatible with this scoring scheme: each team's recorded score equals "
            "the placement points in Methodology plus one point per kill. That match does not establish the event's "
            "official rules. Kills are not added again."
        )
    if scoring == "not_compatible":
        return False, "avg_placement", (
            "These games are not compatible with the placement + kill scheme, so teams are ranked by average placement. "
            "The recorded score is listed on each match and is not used as the rank."
        )
    return False, "avg_placement", (
        "This view mixes games compatible with the placement + kill scheme and games that are not. "
        "Teams are ranked by average placement. Average points are not shown."
    )


def team_leaderboard(connection, filters: dict) -> dict:
    allow_points, rank_by, note = ranking(filters)
    params = scope_params(filters, allow_points=allow_points, rank_by=rank_by)
    with connection.cursor() as cursor:
        cursor.execute(TEAM_LEADERBOARD, params)
        columns = [desc[0] for desc in cursor.description]
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    rows = []
    for index, row in enumerate(raw, start=1):
        flags = []
        if row["name_count"] > 1:
            flags.append("This roster was recorded under more than one name.")
        if row["shared_name"]:
            flags.append("This name is also used by a different roster. Those rosters are not merged.")
        expected = {"trios": 3, "duos": 2, "solos": 1}.get(params["squad_format"] or "")
        if expected is not None and row["min_players"] < expected:
            flags.append("At least one game has fewer recorded players than this squad format.")
        rows.append(
            {
                "rank": index,
                "roster_key": row["roster_key"],
                "team_name": row["team_name"],
                "games": row["games"],
                "sessions": row["sessions"],
                "avg_points": _num(row["avg_points"]),
                "avg_placement": _num(row["avg_placement"]),
                "kills_per_game": _num(row["kills_per_game"]),
                "damage_per_game": _num(row["damage_per_game"]),
                "win_rate": _num(row["win_rate"]),
                "top5_rate": _num(row["top5_rate"]),
                "provisional": row["games"] < 5,
                "flags": flags,
            }
        )
    return {
        "ok": True,
        "rank_by": rank_by,
        "note": note,
        "min_games": params["min_games"],
        "rows": rows,
    }


def team_detail(connection, roster_key: str, filters: dict) -> dict:
    allow_points, rank_by, note = ranking(filters)
    params = scope_params(filters, allow_points=allow_points, rank_by=rank_by)
    params["roster_key"] = roster_key
    with connection.cursor() as cursor:
        cursor.execute(TEAM_DETAIL, params)
        columns = [desc[0] for desc in cursor.description]
        matches = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for match in matches:
        match["points_comparable"] = bool(match["points_comparable"])
    return {"ok": True, "roster_key": roster_key, "matches": matches, "note": note}


def player_leaderboard(connection, filters: dict) -> dict:
    params = scope_params(filters, allow_points=False, rank_by="games")
    with connection.cursor() as cursor:
        cursor.execute(PLAYER_LEADERBOARD, params)
        columns = [desc[0] for desc in cursor.description]
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    rows = []
    for row in raw:
        names = row["team_names"] or []
        rows.append(
            {
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "team_names": list(names),
                "games": row["games"],
                "sessions": row["sessions"],
                "kills_per_game": _num(row["kills_per_game"]),
                "damage_per_game": _num(row["damage_per_game"]),
                "assists_per_game": _num(row["assists_per_game"]),
                "provisional": row["games"] < 5,
            }
        )
    return {
        "ok": True,
        "rows": rows,
        "note": (
            "Kills, damage, and assists are each game's final totals. "
            "Role and survival time change these numbers. This is not a skill rating."
        ),
        "min_games": params["min_games"],
    }


def player_detail(connection, player_id: int, filters: dict) -> dict:
    params = scope_params(filters, allow_points=False, rank_by="games")
    params["player_id"] = int(player_id)
    with connection.cursor() as cursor:
        cursor.execute(PLAYER_DETAIL, params)
        columns = [desc[0] for desc in cursor.description]
        matches = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return {"ok": True, "player_id": int(player_id), "matches": matches}


def filter_options(connection) -> dict:
    from map_config import SHORT_NAMES

    with connection.cursor() as cursor:
        cursor.execute(OPTIONS)
        maps = {}
        sessions = {}
        formats = set()
        for map_name, session_id, event, form in cursor.fetchall():
            if map_name:
                maps[map_name] = SHORT_NAMES.get(map_name, map_name)
            if session_id is not None:
                sessions[session_id] = event or f"Session {session_id}"
            if form:
                formats.add(form)
    return {
        "ok": True,
        "maps": [{"id": key, "label": maps[key]} for key in sorted(maps)],
        "sessions": [
            {"id": key, "label": sessions[key]} for key in sorted(sessions, key=lambda sid: sessions[sid] or "")
        ],
        "formats": sorted(formats),
    }
