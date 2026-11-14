# Tiger Data contract

Dashboard files are unchanged. Ingestion is `tiger_schema.sql`, `tiger_rows.py`, `import_tiger.py`, and `tiger_read.py`.

`DATABASE_URL` is read from the environment. Copy `.env.example` to `.env`. Do not commit `.env` or print the URL.

Schema check against current Tiger Data docs: a hypertable unique key must include the time partitioning column, and that column should be `timestamptz`. `player_frames` uses `PRIMARY KEY (ts, game_id, player_id)` and `create_hypertable` on `ts`.

`ts` is `match_start + t` seconds. `t` is the replay clock. `elapsed_s` is seconds since Playing started.

## Commands

```sh
set -a
. ./.env
set +a
python import_tiger.py --data-dir data --game-id 123349 --dry-run
python import_tiger.py --data-dir data --game-id 123349
python import_tiger.py --data-dir data --game-id 123349
python import_tiger.py --data-dir data --all
```

The second import of the same game deletes that game's frames, squads, and estimates inside one transaction, then inserts them again. Reimport does not add rows.

Database verification is **pending**. `DATABASE_URL` is not set in this environment, so no Tiger connection was opened.

`--dry-run` compares row counts and one frame to the file cache without a database. `psycopg` is imported only when a connection is requested.

## Read interface for Claude

`TigerReader` in `tiger_read.py`:

- `list_matches()` selects `game_id`, `event`, `map_name`, `quality_score`.
- `list_squads(game_id)` selects `team_id`, `team_name`.
- `frame_at_or_before(game_id, t)` uses `max(t)` where `t <=` the request, then that frame's players.
- `prediction_history(game_id, team_id, prediction_kind)` filters one kind: `held_out` or `final_model_fitted`.
- `evaluation_report()` returns the latest held-out aggregate, or `None`.

## Prediction files

Held-out rows come only from `data/models/held_out/<game_id>.json` or `data/models/held_out.json`. Shape: `model_version` plus `games["<game_id>"].points[]` with `t`, `elapsed`, and `squads[]` of `teamId`, `probability`, `baseline_probability`, `in_contention`.

`final_model_fitted` rows are a forward pass of `data/models/model.json` through `features.game_examples`, `train_model.predict`, and `contention_baseline`. They are stored with `prediction_kind=final_model_fitted` only.

If either file is missing, that kind is not inserted. Final-model probabilities are never written as `held_out`.

The primary baseline name stored on every estimate is `uniform_contention`.
