#!/usr/bin/env python3
"""Import final team and player results into Tiger Data.

Does not read or write player_frames, squads, squad_estimates, or
evaluation_reports. Re-running replaces only team_results and player_results.

    .venv/bin/python import_results.py --data-dir data --dry-run
    set -a; . ./.env; set +a
    .venv/bin/python import_results.py --data-dir data --check-reimport
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from import_tiger import SCHEMA, connect, redact
from result_rows import SCORING_COMPATIBLE, load_results

MARKER = "-- Final per-game results"

TEAM_INSERT = """
INSERT INTO team_results (
  game_id, roster_key, team_id, team_name, squad_format, scoring_format,
  points_comparable, placement, kills, assists, damage, score, player_count
) VALUES (
  %(game_id)s, %(roster_key)s, %(team_id)s, %(team_name)s, %(squad_format)s,
  %(scoring_format)s, %(points_comparable)s, %(placement)s, %(kills)s,
  %(assists)s, %(damage)s, %(score)s, %(player_count)s
)
"""

PLAYER_INSERT = """
INSERT INTO player_results (
  game_id, player_id, player_name, team_name, roster_key, squad_format,
  kills, assists, damage
) VALUES (
  %(game_id)s, %(player_id)s, %(player_name)s, %(team_name)s, %(roster_key)s,
  %(squad_format)s, %(kills)s, %(assists)s, %(damage)s
)
"""

COUNT_SQL = """
SELECT
  (SELECT count(*) FROM matches),
  (SELECT count(*) FROM player_frames),
  (SELECT count(*) FROM squad_estimates),
  (SELECT count(*) FROM team_results),
  (SELECT count(*) FROM player_results)
"""


def results_ddl() -> str:
    text = SCHEMA.read_text(encoding="utf-8")
    start = text.index(MARKER)
    return text[start:]


def _counts(connection) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(COUNT_SQL)
        matches, frames, estimates, teams, players = cursor.fetchone()
    return {
        "matches": matches,
        "player_frames": frames,
        "squad_estimates": estimates,
        "team_results": teams,
        "player_results": players,
    }


def apply_results_schema(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(results_ddl())
    connection.commit()


def replace_results(connection, teams: list[dict], players: list[dict]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM player_results")
        cursor.execute("DELETE FROM team_results")
        if teams:
            cursor.executemany(TEAM_INSERT, teams)
        if players:
            cursor.executemany(PLAYER_INSERT, players)
    connection.commit()


def summarize(teams: list[dict], players: list[dict]) -> str:
    games = {row["game_id"] for row in teams}
    compatible = {row["game_id"] for row in teams if row["scoring_format"] == SCORING_COMPATIBLE}
    return (
        f"team rows {len(teams)}, player rows {len(players)}, games {len(games)}, "
        f"compatible with the placement + kill scheme {len(compatible)}, not compatible {len(games - compatible)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import final team and player results.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-reimport", action="store_true")
    args = parser.parse_args(argv)
    teams, players = load_results(args.data_dir)
    print(summarize(teams, players))
    if args.dry_run or not os.environ.get("DATABASE_URL"):
        print("Database verification is pending.")
        return 2 if not args.dry_run else 0
    try:
        connection = connect()
    except SystemExit as exc:
        print(redact(str(exc)))
        return 2
    try:
        apply_results_schema(connection)
        before = _counts(connection)
        replace_results(connection, teams, players)
        after = _counts(connection)
        if after["matches"] != before["matches"] or after["player_frames"] != before["player_frames"]:
            print("replay row counts changed; import stopped being repeatable")
            return 1
        if after["squad_estimates"] != before["squad_estimates"]:
            print("prediction row counts changed; import stopped being repeatable")
            return 1
        print(
            f"imported team_results {after['team_results']} "
            f"(was {before['team_results']}), player_results {after['player_results']}"
        )
        if args.check_reimport:
            replace_results(connection, teams, players)
            again = _counts(connection)
            if again != after:
                print("reimport changed counts")
                return 1
            print("reimport left every count unchanged")
    except Exception as exc:
        print(f"import failed: {type(exc).__name__}: {redact(str(exc).splitlines()[-1])}")
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
