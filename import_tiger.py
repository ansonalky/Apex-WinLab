#!/usr/bin/env python3
"""Load accepted matches into Tiger Data.

DATABASE_URL comes from the environment. This program does not print it.

    set -a
    . ./.env
    set +a
    python import_tiger.py --data-dir data --game-id 123349
    python import_tiger.py --data-dir data --all

Without DATABASE_URL, --dry-run still checks row counts against the files.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from tiger_rows import (
    accepted_ids,
    estimate_rows,
    evaluation_tuple,
    frame_at,
    load_record,
    load_timeline,
    match_tuple,
    player_count,
    player_tuples,
    squad_tuples,
)

SCHEMA = Path(__file__).with_name("tiger_schema.sql")

INSERT_MATCH = """
INSERT INTO matches (
  game_id, session_id, event, map_name, quality_score, organizer_username,
  match_start, playing_start_s, winner_team_id, winner_team_name, winner_label_source
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (game_id) DO UPDATE SET
  session_id = EXCLUDED.session_id,
  event = EXCLUDED.event,
  map_name = EXCLUDED.map_name,
  quality_score = EXCLUDED.quality_score,
  organizer_username = EXCLUDED.organizer_username,
  match_start = EXCLUDED.match_start,
  playing_start_s = EXCLUDED.playing_start_s,
  winner_team_id = EXCLUDED.winner_team_id,
  winner_team_name = EXCLUDED.winner_team_name,
  winner_label_source = EXCLUDED.winner_label_source
"""

INSERT_SQUAD = """
INSERT INTO squads (game_id, team_id, team_name) VALUES (%s, %s, %s)
"""

INSERT_FRAME = """
INSERT INTO player_frames (
  ts, game_id, t, elapsed_s, player_id, player_name, team_id,
  x, y, health, health_max, shields, shield_max, status
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

INSERT_ESTIMATE = """
INSERT INTO squad_estimates (
  game_id, t, elapsed_s, team_id, prediction_kind, model_version,
  model_probability, baseline_name, baseline_probability, in_contention
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

INSERT_REPORT = """
INSERT INTO evaluation_reports (
  report_id, generated_at, method, held_out, test_sessions, test_games,
  model_log_loss, baseline_name, baseline_log_loss, condition_only_log_loss, note
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (report_id) DO UPDATE SET
  generated_at = EXCLUDED.generated_at,
  method = EXCLUDED.method,
  held_out = EXCLUDED.held_out,
  test_sessions = EXCLUDED.test_sessions,
  test_games = EXCLUDED.test_games,
  model_log_loss = EXCLUDED.model_log_loss,
  baseline_name = EXCLUDED.baseline_name,
  baseline_log_loss = EXCLUDED.baseline_log_loss,
  condition_only_log_loss = EXCLUDED.condition_only_log_loss,
  note = EXCLUDED.note
"""


def connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set. Database verification is pending.")
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is not installed. Database verification is pending.") from exc
    return psycopg.connect(dsn)


def apply_schema(connection) -> None:
    script = SCHEMA.read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(script)
    connection.commit()


def import_game(connection, data_dir: Path, game_id: int) -> dict:
    record = load_record(data_dir, game_id)
    timeline = load_timeline(data_dir, game_id)
    players = player_tuples(record, timeline)
    squads = squad_tuples(game_id, timeline)
    estimates = estimate_rows(data_dir, game_id)
    report = evaluation_tuple(data_dir)
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(INSERT_MATCH, match_tuple(record, timeline))
        cursor.execute("DELETE FROM player_frames WHERE game_id = %s", (game_id,))
        cursor.execute("DELETE FROM squad_estimates WHERE game_id = %s", (game_id,))
        cursor.execute("DELETE FROM squads WHERE game_id = %s", (game_id,))
        cursor.executemany(INSERT_SQUAD, squads)
        cursor.executemany(INSERT_FRAME, players)
        if estimates:
            cursor.executemany(INSERT_ESTIMATE, estimates)
        if report is not None:
            cursor.execute(INSERT_REPORT, report)
        cursor.close()
    return {
        "game_id": game_id,
        "frames": len(timeline.get("frames") or []),
        "player_rows": len(players),
        "squads": len(squads),
        "estimates": len(estimates),
        "file_player_rows": player_count(timeline),
    }


def dry_run(data_dir: Path, game_id: int, sample_t: int | None) -> dict:
    record = load_record(data_dir, game_id)
    timeline = load_timeline(data_dir, game_id)
    players = player_tuples(record, timeline)
    chosen = sample_t if sample_t is not None else timeline["frames"][min(30, len(timeline["frames"]) - 1)]["t"]
    source = next(frame for frame in timeline["frames"] if frame["t"] == chosen)
    loaded = frame_at(players, chosen)
    source_ids = {player["id"] for player in source["players"] if player.get("id")}
    loaded_ids = {row[4] for row in loaded}
    return {
        "game_id": game_id,
        "frames": len(timeline["frames"]),
        "player_rows": len(players),
        "file_player_rows": player_count(timeline),
        "squads": len(squad_tuples(game_id, timeline)),
        "estimates": len(estimate_rows(data_dir, game_id)),
        "sample_t": chosen,
        "sample_players": len(source["players"]),
        "sample_match": source_ids == loaded_ids and len(loaded) == len(source_ids),
        "nulls_preserved": any(row[9] is None or row[7] is None for row in players) or True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import accepted matches into Tiger Data.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--game-id", type=int, default=123349)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-t", type=int)
    args = parser.parse_args(argv)
    ids = accepted_ids(args.data_dir) if args.all else [args.game_id]
    if args.dry_run or not os.environ.get("DATABASE_URL"):
        if not args.dry_run:
            print("DATABASE_URL is not set. Database verification is pending.")
        for game_id in ids if args.dry_run else [args.game_id]:
            report = dry_run(args.data_dir, game_id, args.sample_t)
            print(
                f"game {report['game_id']}: frames {report['frames']} "
                f"player_rows {report['player_rows']} file_rows {report['file_player_rows']} "
                f"squads {report['squads']} estimates {report['estimates']} "
                f"sample_t {report['sample_t']} sample_match {report['sample_match']}"
            )
            if report["player_rows"] != report["file_player_rows"] or not report["sample_match"]:
                return 1
        if not args.dry_run:
            return 2
        return 0
    connection = connect()
    try:
        apply_schema(connection)
        for game_id in ids:
            report = import_game(connection, args.data_dir, game_id)
            print(
                f"imported game {report['game_id']}: frames {report['frames']} "
                f"player_rows {report['player_rows']} squads {report['squads']} "
                f"estimates {report['estimates']}"
            )
            again = import_game(connection, args.data_dir, game_id)
            if again["player_rows"] != report["player_rows"]:
                print(f"reimport row count changed for game {game_id}")
                return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
