"""Database reads for the dashboard.

``DATABASE_URL`` is read from the environment and is never printed.

``list_matches()`` -> one row per imported match, with its frozen split.
``match(game_id)`` -> the match row.
``list_squads(game_id)`` -> team id and name.
``frame_times(game_id)`` -> every stored replay second, in order.
``frame_at_or_before(game_id, t)`` -> the latest replay second ``t`` that is
not after the request, with one row per player.
``estimates(game_id, prediction_kind)`` / ``prediction_history(...)`` -> one
kind only. ``held_out`` and ``final_model_fitted`` are never mixed.
``evaluation_report()`` -> the held-out aggregate row, or None.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any

KINDS = ("held_out", "final_model_fitted")

LIST_MATCHES = """
SELECT m.game_id, m.session_id, m.event, m.map_name, m.quality_score, m.organizer_username,
       m.match_start, m.evaluation_split, m.winner_team_id, m.winner_team_name, m.winner_label_source,
       EXISTS (SELECT 1 FROM squad_estimates e WHERE e.game_id = m.game_id AND e.prediction_kind = 'held_out')
FROM matches m
ORDER BY m.match_start NULLS LAST, m.game_id
"""

MATCH = """
SELECT game_id, session_id, event, map_name, quality_score, organizer_username,
       match_start, replay_match_start, playing_start_s, evaluation_split,
       winner_team_id, winner_team_name, winner_label_source, conclusion_t
FROM matches WHERE game_id = %s
"""

LIST_SQUADS = """
SELECT team_id, team_name
FROM squads
WHERE game_id = %s
ORDER BY team_id
"""

FRAME_TIMES = """
SELECT DISTINCT t, elapsed_s FROM player_frames WHERE game_id = %s ORDER BY t
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

ESTIMATES = """
SELECT t, elapsed_s, ts, team_id, model_version, model_probability, baseline_name,
       baseline_probability, in_contention, eliminated, elimination_verified, model_probability_raw
FROM squad_estimates
WHERE game_id = %s AND prediction_kind = %s
ORDER BY t, team_id
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
       condition_only_log_loss, note, generated_at, metrics_json
FROM evaluation_reports
ORDER BY generated_at DESC NULLS LAST
LIMIT 1
"""


class TigerReader:
    """Read-only queries over one reused connection (reopened if it drops)."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
        if not self._dsn:
            raise RuntimeError("DATABASE_URL is not set")
        self._lock = threading.Lock()
        self._conn = None

    @contextmanager
    def _connection(self):
        import psycopg

        with self._lock:
            if self._conn is None or self._conn.closed:
                self._conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=20)
            try:
                yield self._conn
            except psycopg.OperationalError:
                self._conn.close()
                self._conn = None
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def ping(self) -> dict:
        """``SELECT 1`` and the number of imported matches."""
        with self._connection() as connection:
            one = connection.execute("SELECT 1").fetchone()[0]
            exists = connection.execute("SELECT to_regclass('public.matches') IS NOT NULL").fetchone()[0]
            matches = connection.execute("SELECT count(*) FROM matches").fetchone()[0] if exists else 0
        return {"select_1": one, "matches": matches}

    def list_matches(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(LIST_MATCHES).fetchall()
        return [
            {
                "game_id": row[0],
                "session_id": row[1],
                "event": row[2],
                "map_name": row[3],
                "quality_score": row[4],
                "organizer_username": row[5],
                "match_start": int(row[6].timestamp()) if row[6] else None,
                "split": row[7],
                "winner": {"teamId": row[8], "teamName": row[9], "label_source": row[10]},
                "heldout": bool(row[11]),
            }
            for row in rows
        ]

    def match(self, game_id: int) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(MATCH, (game_id,)).fetchone()
        if row is None:
            return None
        keys = ("game_id", "session_id", "event", "map_name", "quality_score", "organizer_username",
                "match_start", "replay_match_start", "playing_start_s", "evaluation_split",
                "winner_team_id", "winner_team_name", "winner_label_source", "conclusion_t")
        return dict(zip(keys, row))

    def list_squads(self, game_id: int) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(LIST_SQUADS, (game_id,)).fetchall()
        return [{"team_id": row[0], "team_name": row[1]} for row in rows]

    def frame_times(self, game_id: int) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(FRAME_TIMES, (game_id,)).fetchall()
        return [{"t": row[0], "elapsed": row[1]} for row in rows]

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

    def estimates(self, game_id: int, prediction_kind: str) -> list[dict]:
        if prediction_kind not in KINDS:
            raise ValueError("prediction_kind must be held_out or final_model_fitted")
        with self._connection() as connection:
            rows = connection.execute(ESTIMATES, (game_id, prediction_kind)).fetchall()
        keys = ("t", "elapsed_s", "ts", "team_id", "model_version", "model_probability", "baseline_name",
                "baseline_probability", "in_contention", "eliminated", "elimination_verified",
                "model_probability_raw")
        return [dict(zip(keys, row)) for row in rows]

    def prediction_history(self, game_id: int, team_id: int, prediction_kind: str) -> list[dict]:
        if prediction_kind not in KINDS:
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
            "metrics_json": row[10],
        }
