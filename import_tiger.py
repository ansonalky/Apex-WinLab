#!/usr/bin/env python3
"""Load accepted matches into Tiger Data, and verify them against local files.

DATABASE_URL comes from the environment. This program never prints it, and
connection errors are redacted.

    set -a; . ./.env; set +a
    python import_tiger.py --data-dir data --game-id 123349 --dry-run
    python import_tiger.py --data-dir data --game-id 123349 --check-reimport
    python import_tiger.py --data-dir data --all

Re-import rules, per game, in one transaction:

- ``matches`` is upserted; ``squads`` and ``player_frames`` are replaced.
- ``squad_estimates`` are replaced **per prediction kind**, and only for a
  kind that has rows this run. A kind whose artifact is missing now keeps
  what was imported before. ``--frames-only`` touches no estimates.
- ``evaluation_reports`` is only ever upserted, never deleted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from tiger_rows import (
    KINDS,
    accepted_ids,
    estimate_rows,
    evaluation_tuple,
    frame_at,
    load_record,
    load_timeline,
    match_tuple,
    player_count,
    player_tuples,
    replay_timestamp,
    squad_tuples,
)

from dashboard_data import HELDOUT_BUNDLE

SCHEMA = Path(__file__).with_name("tiger_schema.sql")

INSERT_MATCH = """
INSERT INTO matches (
  game_id, session_id, event, map_name, quality_score, organizer_username,
  match_start, replay_match_start, playing_start_s, evaluation_split,
  winner_team_id, winner_team_name, winner_label_source, conclusion_t
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (game_id) DO UPDATE SET
  session_id = EXCLUDED.session_id,
  event = EXCLUDED.event,
  map_name = EXCLUDED.map_name,
  quality_score = EXCLUDED.quality_score,
  organizer_username = EXCLUDED.organizer_username,
  match_start = EXCLUDED.match_start,
  replay_match_start = EXCLUDED.replay_match_start,
  playing_start_s = EXCLUDED.playing_start_s,
  evaluation_split = EXCLUDED.evaluation_split,
  winner_team_id = EXCLUDED.winner_team_id,
  winner_team_name = EXCLUDED.winner_team_name,
  winner_label_source = EXCLUDED.winner_label_source,
  conclusion_t = EXCLUDED.conclusion_t
"""

INSERT_SQUAD = "INSERT INTO squads (game_id, team_id, team_name) VALUES (%s, %s, %s)"

COPY_FRAMES = """
COPY player_frames (
  ts, game_id, t, elapsed_s, player_id, player_name, team_id,
  x, y, health, health_max, shields, shield_max, status
) FROM STDIN
"""

INSERT_ESTIMATE = """
INSERT INTO squad_estimates (
  game_id, t, elapsed_s, ts, team_id, prediction_kind, model_version,
  model_probability, model_probability_raw, baseline_name, baseline_probability, in_contention,
  eliminated, elimination_verified
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

INSERT_REPORT = """
INSERT INTO evaluation_reports (
  report_id, generated_at, method, held_out, test_sessions, test_games,
  model_log_loss, baseline_name, baseline_log_loss, condition_only_log_loss, metrics_json, note
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
  metrics_json = EXCLUDED.metrics_json,
  note = EXCLUDED.note
"""


def redact(text: str, dsn: str | None = None) -> str:
    """Remove the connection string, URLs, and host/port from an error message."""
    dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL") or ""
    if dsn:
        text = text.replace(dsn, "<DATABASE_URL>")
    text = re.sub(r"postgres(ql)?://\S+", "<url>", text)
    text = re.sub(r'server at "[^"]+", port \d+', "server at <host>, port <port>", text)
    return text


def connect(dsn: str | None = None):
    dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set. Database verification is pending.")
    import psycopg

    try:
        return psycopg.connect(dsn, connect_timeout=20)
    except psycopg.Error as exc:
        lines = str(exc).splitlines() or [""]
        raise SystemExit(f"database connection failed: {type(exc).__name__}: {redact(lines[-1], dsn)}") from None


def apply_schema(connection, schema: str | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(schema if schema is not None else SCHEMA.read_text(encoding="utf-8"))
    connection.commit()


class Source:
    """Everything one game imports, built from local files once."""

    def __init__(self, data_dir: Path, game_id: int, store=None):
        from dashboard_data import MatchStore

        self.store = store or MatchStore(data_dir)
        self.game_id = game_id
        self.record = load_record(data_dir, game_id)
        self.timeline = load_timeline(data_dir, game_id, self.store)
        split_of = self.store.bundle.get("split_of") or {} if self.store.bundle.get("available") else {}
        self.match = match_tuple(self.record, self.timeline, split_of.get(game_id))
        self.players = player_tuples(self.record, self.timeline)
        self.squads = squad_tuples(game_id, self.timeline)
        self.estimates = estimate_rows(data_dir, game_id, self.timeline, self.store)


def import_game(connection, source: Source, report: tuple | None, frames_only: bool = False) -> dict:
    from psycopg.pq import TransactionStatus

    # Inside an open transaction, transaction() is only a savepoint, and the
    # import is lost when the connection closes. Refuse instead.
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("import_game needs an idle connection; end the open transaction first")
    game_id = source.game_id
    replaced, kept = [], []
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(INSERT_MATCH, source.match)
            cursor.execute("DELETE FROM player_frames WHERE game_id = %s", (game_id,))
            cursor.execute("DELETE FROM squads WHERE game_id = %s", (game_id,))
            cursor.executemany(INSERT_SQUAD, source.squads)
            with cursor.copy(COPY_FRAMES) as copy:
                for row in source.players:
                    copy.write_row(row)
            for kind in KINDS:
                rows, _status = source.estimates[kind]
                if frames_only or not rows:
                    kept.append(kind)
                    continue
                cursor.execute(
                    "DELETE FROM squad_estimates WHERE game_id = %s AND prediction_kind = %s", (game_id, kind)
                )
                cursor.executemany(INSERT_ESTIMATE, rows)
                replaced.append(kind)
            if report is not None and not frames_only:
                cursor.execute(INSERT_REPORT, report)
    return {"game_id": game_id, "replaced_kinds": replaced, "kept_kinds": kept}


def db_counts(connection, game_id: int) -> dict:
    one = lambda sql: connection.execute(sql, (game_id,)).fetchone()[0]
    kinds = dict(
        connection.execute(
            "SELECT prediction_kind, count(*) FROM squad_estimates WHERE game_id = %s GROUP BY 1", (game_id,)
        ).fetchall()
    )
    return {
        "matches": one("SELECT count(*) FROM matches WHERE game_id = %s"),
        "squads": one("SELECT count(*) FROM squads WHERE game_id = %s"),
        "player_frames": one("SELECT count(*) FROM player_frames WHERE game_id = %s"),
        "frame_seconds": one("SELECT count(DISTINCT t) FROM player_frames WHERE game_id = %s"),
        **{f"estimates_{kind}": kinds.get(kind, 0) for kind in KINDS},
        "evaluation_reports": connection.execute("SELECT count(*) FROM evaluation_reports").fetchone()[0],
    }


PLAYER_FIELDS = ("player_name", "team_id", "x", "y", "health", "health_max", "shields", "shield_max", "status")


def verify_game(connection, source: Source, sample_seconds: int = 5) -> dict:
    """Database rows against the local source. Returns counts and every mismatch."""
    game_id = source.game_id
    counts = db_counts(connection, game_id)
    expected = {
        "matches": 1,
        "squads": len(source.squads),
        "player_frames": len(source.players),
        "frame_seconds": len(source.timeline["frames"]),
    }
    for kind in KINDS:
        rows, _status = source.estimates[kind]
        if rows:
            expected[f"estimates_{kind}"] = len(rows)
    problems = [f"{key}: db {counts[key]} vs local {value}" for key, value in expected.items() if counts[key] != value]

    frames = source.timeline["frames"]
    picks = sorted({frames[round(i * (len(frames) - 1) / (sample_seconds - 1))]["t"] for i in range(sample_seconds)})
    replay_start = source.timeline["replay_match_start"]
    manifest_start = source.record["match_start"]
    checked = 0
    for t in picks:
        local = {row[4]: row for row in frame_at(source.players, t)}
        db_rows = connection.execute(
            "SELECT player_id, ts, t, elapsed_s, " + ", ".join(PLAYER_FIELDS)
            + " FROM player_frames WHERE game_id = %s AND t = %s",
            (game_id, t),
        ).fetchall()
        if {row[0] for row in db_rows} != set(local):
            problems.append(f"t {t}: player ids differ")
        for row in db_rows:
            source_row = local.get(row[0])
            if source_row is None:
                continue
            checked += 1
            if row[1] != replay_timestamp(replay_start, t):
                problems.append(f"t {t} {row[0]}: ts {row[1].isoformat()} is not replay match_start + t")
            if row[2:4] != source_row[2:4]:
                problems.append(f"t {t} {row[0]}: t/elapsed differ")
            if tuple(row[4:]) != tuple(source_row[5:]):
                problems.append(f"t {t} {row[0]}: player state differs")
    offset = connection.execute(
        "SELECT min(extract(epoch FROM ts) - t), max(extract(epoch FROM ts) - t) FROM player_frames WHERE game_id = %s",
        (game_id,),
    ).fetchone()
    if offset[0] is None or int(offset[0]) != int(replay_start) or int(offset[1]) != int(replay_start):
        problems.append("ts - t is not the replay match_start on every row")
    held = connection.execute(
        "SELECT count(*) FROM squad_estimates WHERE game_id = %s AND prediction_kind = 'held_out' "
        "AND model_version NOT LIKE %s",
        (game_id, f"%{HELDOUT_BUNDLE}%"),
    ).fetchone()[0]
    if held:
        problems.append(f"{held} held_out rows without the verified bundle as source")
    return {
        "counts": counts,
        "expected": expected,
        "sampled_seconds": picks,
        "sampled_player_rows": checked,
        "ts_minus_t": int(offset[0]) if offset[0] is not None else None,
        "replay_match_start": int(replay_start),
        "manifest_match_start": int(manifest_start),
        "problems": problems,
    }


def dry_run(source: Source) -> dict:
    frames = source.timeline["frames"]
    t = frames[min(30, len(frames) - 1)]["t"]
    sample = next(frame for frame in frames if frame["t"] == t)
    loaded = frame_at(source.players, t)
    return {
        "game_id": source.game_id,
        "frames": len(frames),
        "player_rows": len(source.players),
        "file_player_rows": player_count(source.timeline),
        "squads": len(source.squads),
        "estimates": {kind: (len(rows), status) for kind, (rows, status) in source.estimates.items()},
        "sample_t": t,
        "sample_match": {row[4] for row in loaded} == {p["id"] for p in sample["players"] if p.get("id")},
        "ts_offset_s": int(source.timeline["replay_match_start"]) - int(source.record["match_start"]),
    }


def _print_counts(label: str, counts: dict) -> None:
    print(f"  {label}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import accepted matches into Tiger Data.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--game-id", type=int, default=123349)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Build rows locally; no database")
    parser.add_argument("--check-reimport", action="store_true",
                        help="Import, verify, import again, run a frames-only import, verify counts unchanged")
    parser.add_argument("--frames-only", action="store_true", help="Do not touch estimates or reports")
    args = parser.parse_args(argv)

    from dashboard_data import MatchStore

    store = MatchStore(args.data_dir)
    ids = accepted_ids(args.data_dir) if args.all else [args.game_id]
    if args.dry_run:
        for game_id in ids:
            report = dry_run(Source(args.data_dir, game_id, store))
            print(
                f"game {game_id}: frames {report['frames']} player_rows {report['player_rows']} "
                f"(file {report['file_player_rows']}) squads {report['squads']} sample_match {report['sample_match']} "
                f"replay-vs-manifest start {report['ts_offset_s']:+d}s estimates {report['estimates']}"
            )
            if report["player_rows"] != report["file_player_rows"] or not report["sample_match"]:
                return 1
        return 0

    report_row = None if args.frames_only else evaluation_tuple(args.data_dir)
    connection = connect()
    failures = 0
    try:
        apply_schema(connection)
        for game_id in ids:
            source = Source(args.data_dir, game_id, store)
            result = import_game(connection, source, report_row, args.frames_only)
            check = verify_game(connection, source)
            connection.commit()  # end verify's read transaction so the next import commits on its own
            print(f"game {game_id}: replaced {result['replaced_kinds'] or 'none'}, kept {result['kept_kinds'] or 'none'}")
            _print_counts("db", check["counts"])
            print(
                f"  sampled {check['sampled_player_rows']} player rows at t {check['sampled_seconds']}; "
                f"ts - t = {check['ts_minus_t']} (replay {check['replay_match_start']}, manifest {check['manifest_match_start']})"
            )
            if args.check_reimport:
                before = check["counts"]
                import_game(connection, source, report_row)
                again = db_counts(connection, game_id)
                connection.commit()
                import_game(connection, source, None, frames_only=True)
                frames_only = db_counts(connection, game_id)
                connection.commit()
                _print_counts("after reimport", again)
                _print_counts("after frames-only reimport", frames_only)
                if again != before or frames_only != before:
                    check["problems"].append("counts changed on reimport")
            for problem in check["problems"]:
                print(f"  PROBLEM: {problem}")
            failures += bool(check["problems"])
    finally:
        connection.close()
    print(f"{len(ids)} game(s), {failures} with problems")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
