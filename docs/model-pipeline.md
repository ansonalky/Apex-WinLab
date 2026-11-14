# Model pipeline

`features.py` turns accepted games into per-squad rows at fixed 30-second Playing checkpoints. `train_model.py` fits a conditional-logit winner model and evaluates it on held-out data. Both use the decoder (`overstat_patch`) and the collector's winner verification (`collection.resolve_winner`, `collect_matches.stats_game_for`) without changing them.

## Status (2026-11-14)

First real run on the collection from d7196f5. The synthetic tests check mechanics only; the figures below are the only performance evidence.

**Data checks:**
- 51 accepted games across **22** sessions (HANDOFF.md says 23).
- All 51 raw files match their manifest `sha256`.
- Every `download_path` is a local relative `data/raw/...` path; none point to `/workspace`.
- All 22 sessions have a stats cache, and each game has exactly one standings entry.
- All 51 labels include the standings source, and all 51 pass the `resolve_winner` cross-check.

**Extraction:** 3 games first, then all 51, with no drops: 2,045 checkpoint groups and 37,544 squad rows. **Correction:** an earlier version of this doc said "20 squads per group", which came from the 3-game sample. The real mean is 18.36. Group size is the lobby size: it is constant within a game and equals both the replay's `totalTeams` and the number of squads in that game's standings in all 51 games. No squad is excluded.

| Lobby size | 11 | 13 | 15 | 16 | 17 | 18 | 19 | 20 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Games | 3 | 1 | 2 | 3 | 2 | 7 | 8 | 25 |
| Groups | 119 | 41 | 81 | 120 | 78 | 280 | 319 | 1,007 | 9,816 squad rows have no standing player. The verified winner never has zero standing players at a checkpoint.

**Status semantics on real data:** the number of squads with a standing player equals the replay's own `teamsAlive` at 1,801 of 2,045 checkpoints. It is off by ±1 at 236 and by +2 at 8. Squads went from no standing player back to standing 8 times. Status alone is therefore not a reliable elimination signal, which supports keeping no-standing squads as candidates.

**Session-split evaluation** (split and results preserved from the first run; the audit reproduces them exactly):

| Split | Sessions | Games |
| --- | --- | --- |
| Train | 14 | 36 |
| Validation | 4 | 9 |
| Test | 4 | 6 (238 groups) |

`l2` = 0.01 was chosen on validation. It is the smallest value in the grid, so a wider grid could change it. Every fit converged in 7 Newton steps with `max|grad|` below 1e-7.

| Test, game-weighted | Log loss | Brier | Top-1 |
| --- | --- | --- | --- |
| Model (condition + position) | **2.345** | 0.881 | 0.147 |
| Model, condition only | 2.343 | 0.881 | 0.144 |
| Uniform over squads in contention | 2.419 | 0.895 | 0.105 |
| Uniform over lobby | 2.850 | 0.942 | 0.058 |
| Most standing players | 2.637 | 0.876 | 0.126 |
| *Diagnostic:* model, eliminated squads masked to 0 | 2.341 | 0.880 | 0.147 |

Checkpoint-weighted log loss is within 0.002 of game-weighted for every method.

**Corrected reading.** The earlier 0.505-nat margin was measured against uniform over the whole lobby (mean 18.4 squads). That baseline keeps eliminated squads in play for the whole match. Against uniform over squads still in contention (mean 13.5), the margin is **0.074 nats**. The model beats that baseline on all 6 test games, by 0.013–0.133 nats.

| Elapsed | Model | Uniform (contention) | Uniform (lobby) | Most players |
| --- | --- | --- | --- | --- |
| 0–5 min | 2.760 | 2.834 | 2.850 | 2.668 |
| 5–10 | 2.603 | 2.691 | 2.850 | 3.034 |
| 10–15 | 2.432 | 2.470 | 2.850 | 3.348 |
| 15–20 | 1.553 | 1.651 | 2.850 | 1.467 |

Most of the drop from lobby-uniform comes from knowing which squads are out, not from the model.

**Position features add no measurable value.** The condition-only model was evaluated with the same split and the same validation-based `l2` selection, and test was scored once. Its validation log loss was worse (2.540 vs 2.520). Its test log loss was 2.343 vs 2.345 for the full model. The full model was better on 3 test games and worse on 3. With 6 test games, there is no evidence either way.

**Calibration not reported.** Only 6 games were evaluated; our chosen minimum is 20 (see Evaluation). Six games from four sessions is a small test set. These numbers are a first estimate, not a settled result.

Features that are always tied together (`no_standing` with `center_dist_missing` and `spread_missing`; the four status counts sum to squad size) make individual weights non-interpretable. L2 keeps the fit unique.

## Input contract

`train_model.py --data-dir DATA` reads:

| Input | Required content |
| --- | --- |
| `DATA/collection/manifest.json` | `{"games": {<id>: record}}` |
| record `acceptance_status` | `"accepted"`. Every other value is ignored. |
| record `validation.winner` | `teamId` (not null), `teamName`, `label_source` containing `stats.games[id].teams.overall_stats.teamPlacement`. A label without that standings source is not used. |
| record `game_id`, `session_id` | Group and split keys |
| record `match_start` | Orders sessions oldest to newest. Missing values sort last. |
| record `sha256` | Duplicate-payload check. A record without it is used, and listed under `accepted_missing_sha256`. |
| record `download_path` or `livedata_id` | Raw file at `download_path` (relative to the collector's cwd), else `DATA/raw/<livedata_id>.timeseries.json` |
| `DATA/collection/cache/stats-<session_id>.json` | That session's standings, read with `collect_matches.stats_game_for` (matched by game `id`). Without it the game is dropped. |
| raw timeseries | `{"series": [...]}`, which the decoder replays |

Quality score is not read. Acceptance already applied the quality ≥ 7.0 rule.

## Label

The label is the manifest's verified winner `teamId`. The pipeline cross-checks it the same way collection verified it: it replays the game, calls `resolve_winner(final_state, stats_game)` with this game's standings, and requires the same `teamId`. When standings are missing, `resolve_winner` falls back to the replay-only survivor rule, so the pipeline drops those games (`standings_missing_for_cross_check`) instead of accepting that fallback. A mismatch drops the game (`winner_cross_check_failed`).

## Checkpoints

The replay runs once, frame by frame, with the decoder's `apply_frame` and `export_players`. A test checks these snapshots against `overstat_patch.replay`.

- `start` is the first frame whose reconstructed `state` is `Playing`.
- Checkpoints are `start + 30k` for k = 0, 1, 2, and so on.
- The grid stops at the first frame where `state != "Playing"` or `teamsAlive <= 1`. That frame and everything after it produce no checkpoints.

Both stop signals are visible at the frame where they occur, so a live predictor would see them too. Match length never selects a checkpoint: a 100-second game and a 400-second game have identical snapshots at 10, 40, and 70. A test checks this.

`elapsed = t - start` is stored on each group for reporting. It is not a feature, because a value that's the same for every squad in a group cancels out of a conditional logit.

## Squad eligibility

**Every squad (`teamId`) present in the snapshot is a candidate.** No squad is excluded because of player statuses.

Why: the status strings are `alive`, `downed`, `eliminated`, and `dead`. Their meaning is not defined by the decoder or the patcher. Collection evidence argues against reading `eliminated`/`dead` as "squad is out": in the three trial replays with standings-verified winners, the survivor rule found no squad with a non-`eliminated`/`dead` player at the end. So a winning squad can carry those statuses. Excluding squads with zero standing players could therefore drop the eventual winner. Instead:

- **Downed players** count as standing (`players_downed`). Being downed is not elimination.
- **`eliminated` / `dead`** both count toward `players_out`. Whether either one means respawnable (banner or beacon) or permanently out is **unverified**, so they are not split into separate meanings.
- **Respawn:** a player can go from `dead` back to `alive`. Nothing removes the squad, and the next checkpoint reads the new status. A test covers this with a winner who is `dead` at one checkpoint and alive later.
- **Squads with no standing player** are kept, with `no_standing = 1`. The model can learn how much that matters. The report records how often it happens for the eventual winner (`dataset.eligibility[game].winner_no_standing`). This is the evidence needed before any exclusion rule is adopted.
- **Unknown status** counts toward `players_status_unknown`. It is neither standing nor out.
- **Players without a `teamId`** are not assigned to any squad.

Every group contains its verified winner. If the winner's `teamId` is missing from a snapshot, that checkpoint is skipped and listed (`winner_not_in_snapshot`). It is never relabeled.

### Definitive elimination (evaluation only)

Zero standing players is not the same as elimination. Squad elimination is taken from the feed event `{"type": "eliminated", "team": [player objects]}` up to and including `t`. The event's squad is the single `teamId` shared by its player objects. It is used only to build the contention baseline and the masked diagnostic. It is not a model feature, so the model's predictions are unchanged.

Verified on all 51 games:
- `totalTeams − (squads with an event so far) == teamsAlive` at all 60,677 Playing frames.
- Every non-winning squad has exactly one event during Playing. No event names a squad outside the standings.
- Elimination order matches final standings placement, with zero inversions.
- No player on an eliminated squad is revived or respawned afterward.
- The winner's only events (game 123421, t 1199, one duplicate) fall in `Resolution`, after the checkpoint cutoff.

Residual uncertainty:
- 258 damage, kill or down events are credited to players of already-eliminated squads. 177 come within 10 s. Of the 81 later ones, most are from deployables, the ring, or bleed-out, which keep crediting their owner. About 10 name a gun or grenade, and these are unexplained.
- Player status lags the squad event. 200 squad rows show a standing player after their squad's event, because the status was last written at or before the event and never updated.

At a checkpoint, a group's eliminations count as **verified** only when all of these hold:
- the events are unambiguous and not duplicated;
- `totalTeams − eliminated == teamsAlive`;
- the eventual winner is not among the eliminated;
- every eliminated squad appears in the snapshot.

Otherwise the group is marked **unverified**: no squad is treated as eliminated, and the contention baseline falls back to the lobby. On this data all 2,045 groups verify. Squads in contention equal `teamsAlive` in every group.

Status versus elimination across all squad rows:

| | Eliminated (event) | Not eliminated |
| --- | --- | --- |
| No standing player | 9,762 | 54 |
| Standing player | 200 | 27,528 |

The 54 no-standing, not-eliminated rows are squads whose players are all `eliminated`/`dead` but whose squad event hasn't fired yet. That's consistent with a respawn window. They are kept as candidates.

## Features

Each feature varies between squads within one group.

| Feature | Meaning |
| --- | --- |
| `players_alive`, `players_downed`, `players_out`, `players_status_unknown` | Status counts for the squad |
| `no_standing` | 1 if no player is `alive` or `downed` |
| `hp_frac` (+ `_missing`) | (health + shields) / (max health + max shields) over standing players with all four values known |
| `stale_pos_frac` | Standing players whose x or y is unknown or last written more than 30 s before `t`. Unknown when no player is standing. |
| `center_dist_rel` (+ `_missing`) | Distance from the squad's standing centroid to the centroid of the other squads, divided by the group mean |
| `spread_rel` (+ `_missing`) | Mean distance of standing players from the squad centroid, divided by the group mean |

Coordinate units are undefined, so distances are compared only within one snapshot, and `z` is not used.

## Model and optimization

Conditional logit: `P(squad i wins | group) = softmax_i(w · x_i)`.

Objective: the game-weighted mean negative log-likelihood plus `l2/2 · |w|²`. Each group is weighted `1 / (groups in its game)`, so every game contributes once. With `l2 > 0` the objective is strictly convex and has a single minimum. `fit` refuses `l2 <= 0`.

Preprocessing: per-feature mean and standard deviation over the training rows only. Unknown values become 0 after scaling (the training mean); the `_missing` flag records that the value was unknown.

Optimizer: Newton's method with an exact Hessian and Armijo backtracking. It stops when `max|gradient| < 1e-6`, with at most 100 iterations. Every fit records `converged`, `iterations`, `final_grad_max`, and the loss trace. If any fit does not converge, the report status is `not_converged`, and no metrics or model are written.

Verification (`tests/test_train_model.py`):
- The analytic gradient and Hessian match finite differences.
- The loss trace never rises, and the gradient ends below tolerance.
- Nudging any weight away from the solution increases the objective.
- On 3,000 groups drawn from a known conditional logit, the fit recovers the true weight within 10%, and calibration error is under 0.03. This tests the estimator. It says nothing about real matches.

The softmax summing to 1 does not make the probabilities calibrated. Calibration is measured separately, on held-out data (below).

## Evaluation

**Preferred: session split.** Sessions are ordered by earliest `match_start`. The newest ~20% are test, the next ~20% validation, and the rest train, with at least one session each. `l2` is chosen from {0.01, 0.1, 1.0} by validation log loss, using a model fit on train only. The test model is then refit on train + validation and scored once on test. This requires at least 3 sessions, at least 5 test games, and at least 3 validation games.

**Fallback: `game_held_out_weaker`.** This leave-one-game-out evaluation runs only when a session split isn't possible. `l2` is fixed at 0.1, and preprocessing is refit for every fold. The report carries a warning that held-out games share sessions, lobbies, and teams with training games, so the result is likely optimistic.

Metrics on the test predictions:
- **Primary:** game-weighted log loss, `−ln p(winner)`, averaged as described under weighting.
- **Baselines:**
  - `uniform`: 1/(lobby size) for every squad, all match long.
  - `uniform_contention`: 1/(squads in contention) for squads without a verified elimination, 0 for eliminated squads. This is the meaningful uniform reference.
  - `most_players`: all mass on the squads with the most standing players, split evenly.
  - `model_masked_diagnostic`: the model's probabilities with verified-eliminated squads set to 0 and the rest renormalized. A diagnostic, not a selected model.
- **Brier (per group):** multi-class `Σ_i (p_i − y_i)²` over every squad in the group, where `y_i` is 1 for the winner and 0 otherwise. Range 0 (certain and right) to 2 (certain and wrong). Uniform over `n` squads scores `1 − 1/n`: 0.95 for 20 squads, 0.5 for 2. Brier therefore depends on lobby size and contention, so compare methods only on the same groups.
- **Top-1:** 1 if the winner has the highest probability, with credit split evenly across ties.
- **Weighting:** each group gets weight `1/(groups in its game)`. A game's checkpoints are averaged first, then games are averaged equally, so a 40-checkpoint game counts the same as a 20-checkpoint game. Checkpoint-weighted results (every group weight 1) are reported alongside and labeled. Per-elapsed buckets apply the same game weighting within each bucket.
- **Calibration:** 10 equal-width bins over every squad's predicted probability. Each bin reports game-weighted mean prediction, observed win rate, rows, and games, plus game-weighted ECE. It is reported only when at least 20 games are evaluated. **That minimum is our chosen policy**, not a statistical standard. Below it, many bins would hold a handful of winners, and bin rates would mostly reflect which few games happened to be in test.
- **Ablation:** `condition_only` uses only status counts and health (`players_*`, `no_standing`, `hp_frac`, `hp_frac_missing`). It goes through the same split and validation-based `l2` selection as the full model, and test is scored once.

### Minimum data

Training needs at least 20 usable games that produce labeled groups (`--min-games`, never below 5). Below that, nothing is fit, `evaluation` is null, no `model.json` is written, and the exit code is 2. Tests use 5 or 6 synthetic games to exercise the code paths. The default bar was not lowered.

## Commands

Python 3.10+ (run on 3.12.3), standard library only. No `pip install` is needed. The data directory defaults to `data` relative to the current directory, and `--data-dir` overrides it. Raw `download_path` values are resolved relative to the current directory first, so run from the repository root.

```sh
python3 -m unittest tests.test_features tests.test_train_model tests.test_collection tests.test_overstat_patch -q
python3 features.py --data-dir data     # extraction only -> data/models/extraction.json
python3 train_model.py --data-dir data  # extraction + evaluation + fit -> data/models/report.json, model.json
python3 train_model.py --data-dir /path/to/data --out-dir /path/to/models
```

`features.py` exits 2 when no group was produced. `extraction.json` holds the inventory, drops, and per-game eligibility diagnostics. `report.json` is always written. It contains the inventory, dataset counts, dropped games with reasons, eligibility diagnostics, evaluation, and optimizer state. `model.json` is written only when status is `trained`.

## Frozen evaluation and held-out export

The session split and model settings are frozen in `config/frozen_evaluation.json` (committed). It records:
- the train, validation and test sessions and games;
- `l2` = 0.01 and its validation selection;
- the 12 features and the 30 s checkpoint step;
- each game's raw `sha256`;
- the test metrics.

**Policy:** do not re-select `l2`, features, sessions or baselines against these 6 test games. A new evaluation needs new test sessions.

```sh
python3 export_heldout.py export --data-dir data --out-dir data/exports
```

`export` reads only the frozen config and checks every raw file against its frozen hash. It then:
1. refits the evaluation model on train + validation with the frozen settings;
2. scores the test sessions;
3. writes nothing unless every frozen test metric reproduces within 1e-9.

Output, which is never overwritten: `data/exports/heldout-session-split-v1/` with `bundle.json`, `heldout_predictions.json`, `metrics.json`, `evaluation_model.json` and `README.md`, plus `heldout-session-split-v1.tar.gz` and its `.sha256` for transfer.

- **Held-out vs final:** `prediction_type` is `held_out_session_split_test`, with `held_out: true` and `final_model_predictions_included: false`. Final-model predictions come only from `data/models/model.json`, which is fit on all 51 games; that file is never part of this bundle.
- **Timestamps:** `replay_time_unix = replay match_start + t`. The replay's own `match_start` is 7–16 s later than the manifest's games-API `match_start` in all 51 games. Both are listed per game.
- **Squads:** each checkpoint lists every squad in the lobby with:
  - `team_id`, `team_name`, `is_winner`;
  - `eliminated` and `in_contention`;
  - `standing_players`;
  - probabilities for `model`, `uniform`, `uniform_contention`, `most_players`, and `model_masked_diagnostic`.

## Dashboard handoff: held-out predictions (proposal for the dashboard owner)

The dashboard at 986933e (`docs/dashboard.md`) reads `models/model.json` for a `final_model_fitted` curve and `models/report.json` for aggregate held-out metrics. Its contract has no slot for per-checkpoint held-out predictions, so the export bundle is not shown yet. Proposed adapter, in `dashboard_data.py`:

1. **Discovery:** read `DATA/exports/heldout-session-split-v1/bundle.json`. Accept it only if:
   - `prediction_type == "held_out_session_split_test"`, `held_out` is true, and `final_model_predictions_included` is false;
   - each file's sha256 matches `files`.

   Otherwise show **Not available**.
2. **Join:** match `heldout_predictions.json` entries on `(game_id, t)`, and squads on `team_id` = the dashboard's `teamId`. On all 6 test games, the bundle's `t`, `elapsed_s` and team ids equal the dashboard's own 30 s checkpoints exactly.
3. **Panel:** add a separate "Held-out prediction (test session)" panel. Do not merge it into the final-model curve.
   - Plot `probabilities.model`.
   - Use `probabilities.uniform_contention` as the reference baseline.
   - Mark `eliminated` squads.
   - Label it as evaluation evidence from `evaluation_model.json`, fit on train + validation sessions.
4. **Other games:** for the 45 train and validation games (`frozen_config.games`), show **Not available: game was used for fitting**. Never substitute final-model numbers.
5. **Final-model curve:** keep it as is, labeled `final_model_fitted`. Per the audit, its `uniform` baseline is 1/lobby and overstates the model's edge; `uniform_contention` needs the elimination flags from `features.game_examples`.

## Notes for collection

No collector or decoder change is required. Points the pipeline depends on:

1. Keep `validation.winner.label_source` naming the standings source, and keep `teamId`. Labels without it are skipped.
2. Keep the stats cache at `collection/cache/stats-<session_id>.json` under the data directory. Cross-checking needs it.
3. `download_path` is relative to the collector's cwd. The pipeline falls back to `data/raw/<livedata_id>.timeseries.json`.
4. Write `sha256` on every accepted record.
5. Collecting from more than one session is what makes the session split possible.
