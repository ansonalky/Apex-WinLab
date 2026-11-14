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

**Extraction:** 3 games first, then all 51, with no drops: 2,045 checkpoint groups and 37,544 squad rows (20 squads per group). 9,816 squad rows have no standing player. The verified winner never has zero standing players at a checkpoint.

**Status semantics on real data:** the number of squads with a standing player equals the replay's own `teamsAlive` at 1,801 of 2,045 checkpoints. It is off by ±1 at 236 and by +2 at 8. Squads went from no standing player back to standing 8 times. Status alone is therefore not a reliable elimination signal, which supports keeping no-standing squads as candidates.

**Session-split evaluation:**

| Split | Sessions | Games |
| --- | --- | --- |
| Train | 14 | 36 |
| Validation | 4 | 9 |
| Test | 4 | 6 (238 groups) |

`l2` = 0.01 was chosen on validation. It is the smallest value in the grid, so a wider grid could change it. Every fit converged in 7 Newton steps with `max|grad|` below 1e-7.

| Test, game-weighted | Log loss | Brier | Top-1 |
| --- | --- | --- | --- |
| Model | **2.345** | 0.881 | 0.147 |
| Uniform | 2.850 | 0.942 | 0.058 |
| Most standing players | 2.637 | 0.876 | 0.126 |

Checkpoint-weighted results are within 0.002. By elapsed time, model versus uniform log loss is 2.76/2.85 (0–5 min), 2.60/2.85 (5–10), 2.43/2.85 (10–15), and 1.55/2.85 (15–20). The model beats uniform on each of the 6 test games, by 0.41–0.53 nats. It is slightly worse than the most-players heuristic on Brier, and in the first 5 minutes.

**Calibration not reported:** only 6 test games, and 20 are required. Six games from four sessions is a small test set; these numbers are a first estimate, not a settled result.

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

Uncertain until real data is inspected: whether `teamPlacement` written during play marks squad elimination, and whether the trial's end-of-game `eliminated`/`dead` statuses on winners are a post-match artifact.

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
- **Primary:** game-weighted log loss, compared against **uniform** probabilities over the group's candidate squads.
- Also: Brier score (multi-class, `Σ (p_i − y_i)²`) and top-1 accuracy (credit split across ties), plus the `most_players` heuristic baseline.
- Each metric is reported game-weighted (primary) and checkpoint-weighted (labeled), and broken down by `elapsed` in 5-minute buckets.
- Calibration: 10 equal-width bins over every squad's probability. Each bin reports game-weighted mean prediction, observed win rate, rows, and games, plus game-weighted ECE. Reported only when at least 20 games are evaluated; otherwise `insufficient_data`.

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

## Notes for collection

No collector or decoder change is required. Points the pipeline depends on:

1. Keep `validation.winner.label_source` naming the standings source, and keep `teamId`. Labels without it are skipped.
2. Keep the stats cache at `collection/cache/stats-<session_id>.json` under the data directory. Cross-checking needs it.
3. `download_path` is relative to the collector's cwd. The pipeline falls back to `data/raw/<livedata_id>.timeseries.json`.
4. Write `sha256` on every accepted record.
5. Collecting from more than one session is what makes the session split possible.
