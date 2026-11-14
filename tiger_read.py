"""Database reads for the dashboard. Claude connects these later.

``DATABASE_URL`` is read from the environment and is never printed.

``list_matches()`` -> event, map, quality.
``list_squads(game_id)`` -> team id and name.
``frame_at_or_before(game_id, t)`` -> the latest replay second ``t`` that is
not after the request, with one row per player.
``prediction_history(game_id, team_id, prediction_kind)`` -> one kind only.
``held_out`` and ``final_model_fitted`` are never mixed.
``evaluation_report()`` -> the held-out aggregate row, or None.
"""

from __future__ import annotations

import os
from typing import Any

LIST_MATCHES = """
SELECT game_id, event, map_name, quality_score
FROM matches
ORDER BY match_start NULLS LAST, game_id
"""

LIST_SQUADS = """
SELECT team_id, team_name
FROM squads
WHERE game_id = %s
ORDER BY team_id
"""

FRAME_T = """
SELECT max(t) FROM player_frames
WHERE game_id = %s AND t <= %s
"""

FRAME_ROWS = """
SELECT player_id, player_name, team_id, x, y,
       health, health_max, shields, shield_max, status, t, elapsed_s
FROM player_frames
WHERE game_id = %s AND t = %s
ORDER BY team_id, player_name
"""

PREDICTION_HISTORY = """
SELECT t, elapsed_s, prediction_kind, model_version,
       model_probability, baseline_name, baseline_probability, in_contention
FROM squad_estimates
WHERE game_id = %s AND team_id = %s AND prediction_kind = %s
ORDER BY t
"""

EVALUATION = """
SELECT method, held_out, test_sessions, test_games,
       model_log_loss, baseline_name, baseline_log_loss,
       condition_only_log_loss, note, generated_at
FROM evaluation_reports
ORDER BY generated_at DESC NULLS LAST
LIMIT 1
"""


class TigerReader:
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
        if not self._dsn:
            raise RuntimeError("DATABASE_URL is not set")

    def _connection(self):
        import psycopg

        return psycopg.connect(self._dsn)

    def list_matches(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(LIST_MATCHES).fetchall()
        return [
            {"game_id": row[0], "event": row[1], "map_name": row[2], "quality_score": row[3]}
            for row in rows
        ]

    def list_squads(self, game_id: int) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(LIST_SQUADS, (game_id,)).fetchall()
        return [{"team_id": row[0], "team_name": row[1]} for row in rows]

    def frame_at_or_before(self, game_id: int, t: int) -> dict | None:
        with self._connection() as connection:
            chosen = connection.execute(FRAME_T, (game_id, t)).fetchone()
            if chosen is None or chosen[0] is None:
                return None
            rows = connection.execute(FRAME_ROWS, (game_id, chosen[0])).fetchall()
        return {
            "t": chosen[0],
            "elapsed_s": rows[0][11] if rows else None,
            "players": [
                {
                    "player_id": row[0],
                    "player_name": row[1],
                    "team_id": row[2],
                    "x": row[3],
                    "y": row[4],
                    "health": row[5],
                    "health_max": row[6],
                    "shields": row[7],
                    "shield_max": row[8],
                    "status": row[9],
                }
                for row in rows
            ],
        }

    def prediction_history(self, game_id: int, team_id: int, prediction_kind: str) -> list[dict]:
        if prediction_kind not in ("held_out", "final_model_fitted"):
            raise ValueError("prediction_kind must be held_out or final_model_fitted")
        with self._connection() as connection:
            rows = connection.execute(PREDICTION_HISTORY, (game_id, team_id, prediction_kind)).fetchall()
        return [
            {
                "t": row[0],
                "elapsed_s": row[1],
                "prediction_kind": row[2],
                "model_version": row[3],
                "model_probability": row[4],
                "baseline_name": row[5],
                "baseline_probability": row[6],
                "in_contention": row[7],
            }
            for row in rows
        ]

    def evaluation_report(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(EVALUATION).fetchone()
        if row is None:
            return None
        return {
            "method": row[0],
            "held_out": row[1],
            "test_sessions": row[2],
            "test_games": row[3],
            "model_log_loss": row[4],
            "baseline_name": row[5],
            "baseline_log_loss": row[6],
            "condition_only_log_loss": row[7],
            "note": row[8],
            "generated_at": row[9],
        }
