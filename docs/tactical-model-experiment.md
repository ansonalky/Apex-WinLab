# Tactical-feature experiment

Question: do elevation, observed fire pressure, and revealed ring access improve win prediction beyond squad condition?

This fit is not deployed. It does not replace `data/models`, the frozen evaluation, or exported predictions. The dashboard and decoder were not changed. Telemetry was read from the existing data directory.

Code: `tactical_features.py`, `tactical_experiment.py`. Report: `experiments/tactical-model/report.json`. Generated 6:17 PM PT on November 14, 2026.

## What was compared

Same 30-second checkpoints as the frozen pipeline (`features.build_dataset`), on the frozen train and validation sessions only: **45 games, 18 sessions, 1,807 checkpoints**. The six test games (123633, 123639, 123644, 123658, 123659, 123662) were excluded and the script refuses to continue if one appears.

`l2` is the frozen value **0.01**. It was not re-selected. Each fold calls `train_model.fit`, which standardizes using that fold's training rows only. A session's squads and timestamps are never split across train and held-out.

The condition baseline is corrected here and nowhere else. Standing combat health is health plus shields of `alive` players. Downed bleed is the downed player's health over max health. A downed 100/100 bleed pool does not increase standing combat health. The frozen `hp_frac`, which mixes those pools, is not an input.

| Arm | Added measurements, from `tactical.measure` |
| --- | --- |
| Corrected condition | Status counts, standing combat health, downed bleed, and missing flags |
| Condition + elevation | Mean height gap versus standing opponents within 8,000 replay units, plus higher/lower counts |
| Condition + pressure | Opponent damage and distinct attacking squads in the last 10 seconds, near/medium squads, occupied direction sectors, nearest distance, stale opponents excluded, fraction of standing members with a fresh position |
| Condition + ring access | Fraction of fresh members outside the revealed ring, and mean signed boundary distance divided by radius |
| Combined | All of the above |

Unknowns stay missing, with a `*_missing` flag. Zero incoming damage is a real zero. No line of sight, cover, or route is invented. Ring geometry uses only stages already revealed, the same rule as `tactical.py`.

## Representation

The model is an additive conditional logit. A squad's score is a weighted sum of its own columns, then a softmax at that timestamp. Weights were not constrained. The train-set weight on standardized `elev_mean_dz` was **+0.026** with elevation alone and **+0.031** in the combined fit (positive means higher relative z raises that squad's score). That sign was learned, not imposed.

This representation cannot express "height matters only when enemies are close" or "only while the ring is closing," because elevation is not multiplied by pressure or ring state. A column that is the same for every squad at one timestamp cancels in the softmax, so ring phase by itself is not used.

## Coverage (33,418 squad-rows)

| Measurement | Rows known |
| --- | --- |
| Standing combat health | 72.6% |
| Fresh squad position | 74.1% |
| Nearest fresh opposing squad | 67.1% |
| Revealed ring access | 55.9% |
| Elevation versus a nearby squad | 43.7% |

## Scores

Session-grouped log loss and Brier: each game's checkpoints are averaged, then games in a session are averaged, then sessions are averaged. Probabilities are eligibility-constrained (a verified elimination is exactly 0). Lower is better. Differences are the arm minus corrected condition. A negative difference is an improvement. Intervals are a 2,000-draw bootstrap over sessions.

### Train sessions, scored on the 4 validation sessions

| Arm | Log loss | Brier | Log-loss difference | Sessions better / worse |
| --- | --- | --- | --- | --- |
| Corrected condition | 2.533 | 0.898 |  |  |
| + elevation | 2.550 | 0.903 | +0.017 (CI −0.015 to +0.048) | 2 / 2 |
| + pressure | 2.531 | 0.898 | −0.002 (CI −0.057 to +0.038) | 1 / 3 |
| + ring access | 2.525 | 0.897 | −0.008 (CI −0.029 to +0.010) | 3 / 1 |
| Combined | 2.538 | 0.902 | +0.005 (CI −0.061 to +0.064) | 1 / 3 |

Validation log-loss intervals all include zero. Elevation's Brier was worse in all 4 sessions (difference +0.005, CI +0.002 to +0.008).

### Leave-one-session-out, 18 development sessions

| Arm | Log loss | Brier | Log-loss difference | Sessions better / worse |
| --- | --- | --- | --- | --- |
| Corrected condition | 2.460 | 0.891 |  |  |
| + elevation | 2.470 | 0.893 | **+0.009 (CI +0.002 to +0.018)** | 6 / 12 |
| + pressure | 2.466 | 0.891 | +0.006 (CI −0.008 to +0.018) | 9 / 9 |
| + ring access | 2.459 | 0.891 | −0.002 (CI −0.009 to +0.005) | 9 / 9 |
| Combined | 2.474 | 0.893 | +0.014 (CI −0.004 to +0.031) | 7 / 11 |

Elevation's log loss and Brier both get worse, and those intervals exclude zero. Combined Brier also gets worse (difference +0.003, CI just above zero, 6 sessions better and 12 worse). Combined log loss leans worse, but that interval includes zero. Pressure and ring access are split across sessions; their intervals include zero. Ring access is the only negative mean log-loss difference, and it is about 0.002 nats.

## Where the combined fit lost the most

Leave-one-session-out log loss, combined minus condition:

| Session | Event | Games | Difference |
| --- | --- | --- | --- |
| 23240 | ASCL LCQ Scrims Match Point 13/11 | 3 | +0.084 |
| 23262 | Leadjoy Eternal Cup, Unlimited Cup Group A | 4 | +0.072 |
| 23257 | ASCL LCQ Scrims Match Point 14/11 | 2 | +0.058 |
| 23232 | S5: Premier Lobby2 Week 6 | 2 | +0.044 |
| 23222 | YLGS C组小组赛 | 3 | +0.041 |

These are not explanations of individual fights. They are the sessions where adding the tactical columns hurt the session score the most.

## Time

Feature attachment 51 seconds. The 19 folds (one train/validation fit and 18 leave-one-session fits), five arms each, 378 seconds. Total 439 seconds.

## Reading

On these development sessions, elevation, observed pressure, and revealed ring access do not improve prediction beyond corrected squad condition. The one consistent change is that relative elevation makes the session scores worse, even though the training weight on height was slightly positive. The other additions are uncertain. The additive logit also cannot represent a height effect that depends on who is nearby.

## Reproduce

```sh
# from the tactical-model-experiment worktree, telemetry read-only
.venv/bin/python -m unittest tests.test_tactical_features -q
.venv/bin/python tactical_experiment.py --data-dir /path/to/data
```
