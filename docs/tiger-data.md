# Tiger Data contract

Ingestion is `tiger_schema.sql`, `tiger_rows.py`, and `import_tiger.py`. The dashboard read path is `tiger_read.py` (`TigerReader`) and `tiger_store.py` (`TigerStore`).

`DATABASE_URL` is read from the environment, which is loaded from `.env`. It is never printed, and connection errors are redacted (no URL, host, or port).

## Status (2026-11-14)

**Tiger Data has not been reached.** The only `DATABASE_URL` on this machine, in `~/Apex-WinLab/.env`, has a username but no password. `SELECT 1` fails with `fe_sendauth: no password supplied`. No `.env` exists in this worktree.

**The SQL was verified on a local stand-in:** embedded PostgreSQL via `pgserver`, without TimescaleDB, so the extension and `create_hypertable` lines were skipped. Games 123349 (train) and 123633 (held-out test):
- Database counts equal the local source.
- Sampled player rows match field by field.
- `ts − t` equals the replay's `match_start` on every row.
- Full, frames-only, and missing-artifact re-imports leave every count unchanged.
- The database-backed dashboard store returns the same frames, checkpoints, and predictions as the file store.

This is not a Tiger verification.

## Time

`player_frames.ts` and `squad_estimates.ts` are the **reconstructed replay's** `match_start` + `t`. `matches.replay_match_start` stores that start time. `matches.match_start` is the games-API time from the manifest. It is 7–16 s earlier on every accepted game and is never used for `ts`. Import refuses a game whose replay start time is missing.

## Predictions and provenance

| `prediction_kind` | Source | Games |
| --- | --- | --- |
| `held_out` | `data/exports/heldout-session-split-v2/` only, validated by `dashboard_data.load_heldout_bundle` (labels and sha256) and joined on `(game_id, t, teamId)` | The 6 test games |
| `final_model_fitted` | Forward pass of `data/models/model.json` | All accepted games (all of them were in its fit) |

- `model_probability` is **eligibility-constrained**: squads with a verified elimination at or before `t` get exactly 0, and the rest are renormalized.
- `model_probability_raw` keeps the unconstrained output.
- `eliminated`, `elimination_verified`, and `in_contention` describe the prediction time, not later.
- `model_version` is JSON provenance: source path, bundle hash and commit, or model settings.
- `matches.evaluation_split` stores the frozen split, so the dashboard can say why held-out rows are absent.

## Re-import

Each game is one transaction:
- `matches` is upserted.
- `squads` and `player_frames` are replaced.
- `squad_estimates` are replaced **per kind, only when that kind has rows in this run**. A kind whose artifact is missing keeps its stored rows. `--frames-only` touches no estimates or reports.
- `evaluation_reports` is only ever upserted. Its metrics must equal `config/frozen_evaluation.json`, or it is not imported.

## Commands

Run from the repository root.

```sh
.venv/bin/python -m pip install -r requirements.txt
set -a; . ./.env; set +a
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --dry-run
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --check-reimport
.venv/bin/python import_tiger.py --data-dir data --all
.venv/bin/python app.py --data-dir data --port 5050 --backend auto
```

Every import verifies database counts and sampled player states against the local files and prints any problem. `--check-reimport` also runs a second import and a frames-only import, and requires all counts to stay unchanged.

## Dashboard backend

- `--backend tiger` reads through `TigerReader` and exits if the database is unusable.
- `--backend files` reads local files.
- `--backend auto` uses Tiger when `SELECT 1` succeeds and matches are imported; otherwise it uses files and says why.

The page header and `/api/backend` always show the active backend and the reason.
