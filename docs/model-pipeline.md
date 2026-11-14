# Model pipeline

`features.py` turns accepted games into per-team rows at Playing checkpoints. `train_model.py` fits a winner model on those rows and evaluates it on held-out games. Both use the existing decoder (`overstat_patch.replay`) and the collector's acceptance rules (`collection.winning_squad`) unchanged.

## Status (2026-11-14)

**No model has been trained. No evaluation results exist.** The data check found no `data/collection/manifest.json` in this worktree and no raw timeseries. The collection trial in HANDOFF.md accepted 0 of 6 games in session 23231. `train_model.py` therefore stops with `status: insufficient_data` and writes no metrics.

## Data in

Only manifest records with `acceptance_status == "accepted"` are used, so the quality ≥ 7.0 gate and replay validation are inherited from collection. The raw file is found from `download_path`, then from `data/raw/<livedata_id>.timeseries.json`. Records whose `sha256` repeats an earlier record are dropped as duplicate payloads. An accepted record without a raw file is listed in the report, not silently skipped.

Quality score is not a feature.

## Rows and labels

For each game the decoder replays the series. Checkpoints are the decoder's `default_checkpoints` (start, midpoint, end of Playing); `--checkpoints N` spaces N evenly over the same span.

One **group** is one checkpoint of one game. Its rows are the teams with at least one player not `eliminated` or `dead` (a player with unknown status counts as standing). The label is the team that `winning_squad` finds in the final reconstructed state. Team identity is `teamId`, else `teamName`.

A game is dropped when the outcome is unknown, the replay has patch failures, or the recomputed winner disagrees with the manifest's `validation.winner`. A checkpoint is dropped when the winner is not among its standing teams.

Every feature comes from `export_players` at the checkpoint `t`. A later write cannot change a row; `tests/test_features.py` checks this.

| Feature | Meaning |
| --- | --- |
| `progress` | `(t - playing_start) / (playing_end - playing_start)` |
| `teams_standing` | Candidate teams in this group |
| `players_alive`, `players_downed`, `players_out` | Status counts for the team |
| `alive_frac` | `players_alive / team size` |
| `hp_frac` | (health + shields) / (max health + max shields) over standing players with all four values known; unknown when none are |
| `stale_pos_frac` | Standing players whose x or y is unknown or last written more than 30 s before `t` |
| `center_dist_rel` | Distance from the team's horizontal centroid to the centroid of all other standing teams, divided by the group mean |
| `spread_rel` | Mean distance of the team's players from its centroid, divided by the group mean |
| `*_missing` | 1 when the matching value is unknown |

Coordinate units are undefined, so distances are only compared within a snapshot, and `z` is not used.

## Model

Conditional logit: each team scores `w · x`; win probabilities are the softmax within the group. L2-regularized, full-batch gradient descent, standard library only. Unknown values are imputed to the training-fold mean after standardization (that is, 0), with the `*_missing` indicator carrying the fact that it was unknown.

## Evaluation

Leave-one-game-out. All checkpoints of a game sit in one fold, and the scaler and weights are fit on the other games only. Reported per checkpoint index and overall:

- `log_loss` of the winner's probability
- `top1`: winner has the highest probability, with credit split across ties

Two baselines are scored on the same groups: `uniform` over standing teams, and `most_players` (all mass on the teams with the most standing players).

### Minimum data

Training and evaluation need at least **20** games that produce labeled rows (`--min-games`, never below 5). Below that, the report has `status: insufficient_data`, `evaluation: null`, no `model.json`, and the exit code is 2. At 20 games one game still moves top-1 by 5 points, so results near the bar should be read with that in mind.

Games are the independent unit. Games from one session share teams and lobbies, so they are not fully independent; the report lists `sessions`. HANDOFF.md notes 50 accepted games will need more than one session.

## Commands

```sh
python3 -m unittest tests.test_features tests.test_train_model -q
python3 train_model.py                       # data/ -> data/models/report.json
python3 train_model.py --checkpoints 5 --min-games 30
```

Outputs, both under the git-ignored `data/models/`: `report.json` (always) with the inventory, dataset counts, dropped games, and evaluation; `model.json` (only when trained) with feature names, weights, scaler, and trained game ids.

## Interface notes for collection

No decoder or collector change is required for this pipeline to run. Requests that would help:

1. **Winner coverage.** `winning_squad` needs `teamsAlive == 1` and exactly one non-out team. Three of three new replays in the trial failed this (`outcome_unknown`). Until the collector can identify more winners, almost nothing reaches the model.
2. **`download_path` is relative to the collector's working directory.** `features.raw_path` falls back to `data_dir/raw/<livedata_id>.timeseries.json`. A path relative to `data_dir`, or the fixed layout documented as the contract, would remove the guess.
3. **`validation.winner` carries `teamId`/`teamName`.** The pipeline cross-checks it against its own replay. Keep those field names stable.
4. **`sha256` on every accepted record.** Duplicate-payload detection depends on it; a record without it is never treated as a duplicate.
