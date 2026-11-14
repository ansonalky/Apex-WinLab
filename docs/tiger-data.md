# Tiger Data contract

Ingestion is `tiger_schema.sql`, `tiger_rows.py`, and `import_tiger.py`. The dashboard read path is `tiger_read.py` (`TigerReader`) and `tiger_store.py` (`TigerStore`).

`DATABASE_URL` is read from the environment, which is loaded from `.env`. It is never printed, and connection errors are redacted (no URL, host, or port).

## Status (2026-11-14)

**Imported into Tiger Data** (TimescaleDB 2.32.0). `player_frames` is a hypertable on `ts` with 1-day chunks.

| Table | Rows |
| --- | --- |
| `matches` | 51 (test 6, train 36, validation 9). `conclusion_t` is the first `WinnerDetermined` second, recorded for all 51 |
| `squads` | 936 |
| `player_frames` | 3,312,521 (Playing start to the end of the telemetry, including the frames after `WinnerDetermined`) |
| `squad_estimates` | 41,670: `final_model_fitted` 37,544 (51 games), `held_out` 4,126 (6 games) |
| `evaluation_reports` | 1 |

Checks, read back from a new connection:
- `--check-reimport` on 123349 leaves every count unchanged.
- No duplicate `(ts, game_id, player_id)` rows.
- Sampled frames of 123349 (train) and 123633 (held-out) equal the file store.
- With `--backend tiger`, the dashboard API matches the file store across 320 responses over all 51 games. Squads, positions, health, checkpoints, eligibility, every model and baseline probability, per-game held-out metrics, and session counts all match. Differences remaining:
  - wording of limitation and reason text;
  - calibration status, which is not stored;
  - fields the page does not read: `most_players`, `uniform`, `teams_alive`, bundle metadata, and `converged`.

Per-game held-out metrics are not stored. `TigerStore` recomputes them from stored probabilities and `matches.winner_team_id` with `train_model.score`, and they equal the export bundle's values.

An earlier importer bug committed only the first game of an `--all` run. Verification left a read transaction open, so each later import ran as a savepoint and was rolled back when the connection closed. `main` now ends that transaction after each check, and `import_game` refuses to run inside an open transaction.

Map and ring are **not** in the database. Under every backend they come from `map_config.py`, `static/maps/`, and the local replay payloads (see `docs/dashboard.md`).

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
