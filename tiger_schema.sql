-- Tiger Data / TimescaleDB schema for the dashboard read path.
-- Unique indexes include the time partitioning column, which current
-- Tiger Data docs require:
-- https://www.tigerdata.com/docs/build/performance-optimization/hypertables-and-unique-indexes
-- https://www.tigerdata.com/docs/learn/data-model/primary-keys-time-and-uniqueness
-- The time column is timestamptz, the type those docs prefer.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS matches (
  game_id              integer PRIMARY KEY,
  session_id           integer NOT NULL,
  event                text,
  map_name             text,
  quality_score        double precision,
  organizer_username   text,
  match_start          timestamptz,   -- games API time (manifest); not the replay clock
  replay_match_start   timestamptz,   -- the replay's own match_start; player_frames.ts = this + t
  playing_start_s      integer,
  evaluation_split     text,          -- frozen session split: train, validation, test
  winner_team_id       integer,
  winner_team_name     text,
  winner_label_source  text
);

-- Replay second of the first WinnerDetermined state; null if the replay ends before it.
ALTER TABLE matches ADD COLUMN IF NOT EXISTS conclusion_t integer;

CREATE TABLE IF NOT EXISTS squads (
  game_id    integer NOT NULL REFERENCES matches (game_id),
  team_id    integer NOT NULL,
  team_name  text,
  PRIMARY KEY (game_id, team_id)
);

CREATE TABLE IF NOT EXISTS player_frames (
  ts           timestamptz NOT NULL,
  game_id      integer NOT NULL REFERENCES matches (game_id),
  t            integer NOT NULL,
  elapsed_s    integer NOT NULL,
  player_id    text NOT NULL,
  player_name  text,
  team_id      integer,
  x            double precision,
  y            double precision,
  health       double precision,
  health_max   double precision,
  shields      double precision,
  shield_max   double precision,
  status       text,
  PRIMARY KEY (ts, game_id, player_id)
);

SELECT create_hypertable(
  'player_frames',
  'ts',
  chunk_time_interval => INTERVAL '1 day',
  if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS player_frames_game_t
  ON player_frames (game_id, t DESC);

CREATE TABLE IF NOT EXISTS squad_estimates (
  game_id                integer NOT NULL REFERENCES matches (game_id),
  t                      integer NOT NULL,
  elapsed_s              integer NOT NULL,
  ts                     timestamptz,   -- replay match_start + t
  team_id                integer NOT NULL,
  prediction_kind        text NOT NULL,
  model_version          text,          -- JSON provenance: source file, fit, hashes
  model_probability      double precision,  -- eligibility-constrained (eliminated squads exactly 0)
  model_probability_raw  double precision,  -- conditional-logit output before the eligibility rule
  baseline_name          text NOT NULL,
  baseline_probability   double precision,
  in_contention          boolean,
  eliminated             boolean,
  elimination_verified   boolean,
  PRIMARY KEY (game_id, t, team_id, prediction_kind),
  CONSTRAINT squad_estimates_kind
    CHECK (prediction_kind IN ('held_out', 'final_model_fitted'))
);

CREATE TABLE IF NOT EXISTS evaluation_reports (
  report_id                 integer PRIMARY KEY,
  generated_at              timestamptz,
  method                    text NOT NULL,
  held_out                  boolean NOT NULL,
  test_sessions             integer,
  test_games                integer,
  model_log_loss            double precision,
  baseline_name             text NOT NULL,
  baseline_log_loss         double precision,
  condition_only_log_loss   double precision,
  metrics_json              text,      -- game-weighted test metrics for every method
  note                      text NOT NULL
);

-- Columns added after the first draft of this schema.
ALTER TABLE matches ADD COLUMN IF NOT EXISTS replay_match_start timestamptz;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS evaluation_split text;
ALTER TABLE squad_estimates ADD COLUMN IF NOT EXISTS ts timestamptz;
ALTER TABLE squad_estimates ADD COLUMN IF NOT EXISTS model_probability_raw double precision;
ALTER TABLE squad_estimates ADD COLUMN IF NOT EXISTS eliminated boolean;
ALTER TABLE squad_estimates ADD COLUMN IF NOT EXISTS elimination_verified boolean;
ALTER TABLE evaluation_reports ADD COLUMN IF NOT EXISTS metrics_json text;
