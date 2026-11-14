# Tiger Data integration plan

Tiger Data is the Postgres service. The dashboard keeps reading a local `--data-dir` until this load exists. No model or decoder file changes. Credentials stay in a gitignored `.env` as `DATABASE_URL`. Do not put a password in this file, in shell history, or in Git.

The first import target is the match the dashboard already serves: game `123349`, Wednesday Customs, map `mp_rr_district_mu5`.

## Tables

`matches` is the selector. Quality and the verified winner are labels, not model features.

```sql
CREATE TABLE matches (
  game_id            integer PRIMARY KEY,
  session_id         integer NOT NULL,
  event              text,
  map_name           text,
  quality_score      double precision,
  organizer_username text,
  match_start        timestamptz,
  playing_start_s    integer,
  winner_team_id     integer,
  winner_team_name   text,
  winner_label_source text
);
```

`squads` is one row per team in that match.

```sql
CREATE TABLE squads (
  game_id   integer NOT NULL REFERENCES matches (game_id),
  team_id   integer NOT NULL,
  team_name text NOT NULL,
  PRIMARY KEY (game_id, team_id)
);
```

`player_frames` is the slider. `ts` is `match_start + t seconds`. `elapsed_s` is seconds since Playing started (`t - playing_start_s`), which the page formats as MM:SS. `x` and `y` are nullable. Missing health, shields, and status stay null, not zero.

```sql
CREATE TABLE player_frames (
  ts          timestamptz NOT NULL,
  game_id     integer NOT NULL REFERENCES matches (game_id),
  t           integer NOT NULL,
  elapsed_s   integer NOT NULL,
  player_id   text NOT NULL,
  player_name text,
  team_id     integer,
  x           double precision,
  y           double precision,
  health      double precision,
  health_max  double precision,
  shields     double precision,
  shield_max  double precision,
  status      text,
  PRIMARY KEY (ts, game_id, player_id)
);

SELECT create_hypertable('player_frames', 'ts', chunk_time_interval => INTERVAL '1 day');
```

`squad_estimates` is empty until `data/models/model.json` exists. `prediction_type` must be `final_model_fitted` for a curve from that file. Held-out checkpoint probabilities are not in `report.json`, so they are not inserted. The primary baseline is `uniform_contention` (equal mass on squads not yet verified eliminated).

```sql
CREATE TABLE squad_estimates (
  game_id              integer NOT NULL REFERENCES matches (game_id),
  t                    integer NOT NULL,
  elapsed_s            integer NOT NULL,
  team_id              integer NOT NULL,
  prediction_type      text NOT NULL,
  model_probability    double precision,
  baseline_name        text NOT NULL DEFAULT 'uniform_contention',
  baseline_probability double precision,
  in_contention        boolean,
  PRIMARY KEY (game_id, t, team_id, prediction_type)
);
```

`evaluation_reports` is one row from `data/models/report.json` when that file exists and `evaluation.status` is `ok`. It is not joined to the slider.

```sql
CREATE TABLE evaluation_reports (
  report_id            integer PRIMARY KEY,
  generated_at         timestamptz,
  method               text NOT NULL,
  held_out             boolean NOT NULL,
  test_sessions        integer,
  test_games           integer,
  model_log_loss       double precision,
  baseline_name        text NOT NULL,
  baseline_log_loss    double precision,
  condition_only_log_loss double precision,
  note                 text NOT NULL
);
```

The audited held-out row, once the report file is loaded, is method `session_split`, 4 test sessions, 6 test games, full-model log loss 2.345, baseline `uniform_contention` log loss 2.419. `condition_only_log_loss` records that positional features showed no added benefit on that run. If the file is absent, insert nothing and the page keeps saying Not available.

## Import command

`.env` is already gitignored. `DATABASE_URL` is the only secret.

```sh
set -a
. ./.env
set +a
python import_tiger.py --data-dir data --game-id 123349
```

That command is the contract. `import_tiger.py` is not in the tree yet, so Claude's current dashboard files stay as they are. The loader should:

1. Read `DATABASE_URL` from the environment and exit if it is missing.
2. Read `data/collection/manifest.json` for accepted game `123349` only.
3. Replay once with the existing dashboard cache (`data/dashboard/123349.json` if present, otherwise one `build_timeline` pass) and `COPY` into `matches`, `squads`, and `player_frames`.
4. Load `data/models/model.json` and `data/models/report.json` only when those files exist. Do not fit a model and do not write probability rows without `model.json`.
5. Be safe to run again: delete that `game_id`'s frame and estimate rows, then insert.

## Queries the dashboard will use

Match selector:

```sql
SELECT game_id, event, map_name, quality_score
FROM matches
ORDER BY match_start NULLS LAST, game_id;
```

Squad selector:

```sql
SELECT team_id, team_name
FROM squads
WHERE game_id = $1
ORDER BY team_id;
```

Time slider:

```sql
SELECT t, elapsed_s
FROM player_frames
WHERE game_id = $1
GROUP BY t, elapsed_s
ORDER BY t;
```

Position, health, shields, and status at the selected time:

```sql
SELECT player_id, player_name, team_id, x, y,
       health, health_max, shields, shield_max, status
FROM player_frames
WHERE game_id = $1 AND t = $2
ORDER BY team_id, player_name;
```

Experimental win estimate for the selected squad. Zero rows means Not available. This is a final-model fitted curve, not a held-out test, and not a decision effect.

```sql
SELECT elapsed_s, model_probability, baseline_probability
FROM squad_estimates
WHERE game_id = $1
  AND team_id = $2
  AND prediction_type = 'final_model_fitted'
  AND baseline_name = 'uniform_contention'
ORDER BY elapsed_s;
```

Held-out panel, separate from the match:

```sql
SELECT method, held_out, test_sessions, test_games,
       model_log_loss, baseline_name, baseline_log_loss,
       condition_only_log_loss, note
FROM evaluation_reports
ORDER BY generated_at DESC
LIMIT 1;
```

## Artifact contract for Claude

Place these under the same `--data-dir` the dashboard already uses. Do not commit them.

| File | Required contents |
| --- | --- |
| `collection/manifest.json` | Accepted record with `game_id`, `event`, `map_name`, `quality_score`, `session_id`, `match_start`, `download_path` or `livedata_id`, and `validation.winner` (`teamId`, `teamName`, `label_source`) |
| `raw/<livedata_id>.timeseries.json` | Unmodified replay payload |
| `dashboard/<game_id>.json` | Optional cache from one replay. Fields: `playing_start`, `frames[].t`, `frames[].elapsed`, `frames[].players[]` with `id`, `name`, `teamId`, `teamName`, `x`, `y`, `health`, `healthMax`, `shields`, `shieldMax`, `status` |
| `models/model.json` | Final fit only: `weights`, `scaler`, `features`, `l2`, `trained_on_games`, `checkpoint_step_s`. Absence means no curve |
| `models/report.json` | `evaluation.status = ok`, `evaluation.method`, `evaluation.games.test`, `evaluation.sessions.test`, and `evaluation.test.overall.game_weighted` for `model` and `uniform_contention`. `ablation.condition_only` carries the no-position comparison. Absence means no held-out panel |

Per-checkpoint held-out probabilities are not part of the contract. The curve and the held-out table stay on different reads.
