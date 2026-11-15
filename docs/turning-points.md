# Largest changes in model estimate

`turning_points.py` finds substantial moves in a squad's per-second win estimate. It does not fit a model, and it does not change the frozen evaluation or the replay page.

The label on every highlight is **Largest changes in model estimate.**

## Prediction interface

Per-second values come from `live_predictions.LivePredictions`:

- `table(game_id, kind)` maps each replay second to squads.
- The probability used here is `squads[].model`, the eligibility-constrained value. `model_unconstrained` is not mixed in.
- `kind` is `held_out` or `final_model_fitted`. One call uses one kind. Rows must also share one model file (`provenance["sha256"]`).
- Seconds run from Playing start until the feature cut-off. The cut-off is the first second that is not Playing, or that has `teamsAlive <= 1`. There is no prediction at or after that second.
- Nothing is interpolated. A window of 5, 10, or 20 seconds is kept only when every integer replay second in it has a prediction for that squad.

The dashboard's jump to 100% at `WinnerDetermined` is a recorded result, not a model output. Pass that second as `conclusion_t`, or mark those rows `recorded_outcome`. They are dropped before windows are measured. `highlights_for_game` uses the live table's cut-off as that stop.

## Highlights

Changes are percentage points: `(after - before) * 100`. The default minimum is 5 points. Overlapping windows for the same squad and the same direction are one episode. The returned window is the one with the largest absolute change; a tie keeps the shorter window, then the earlier start. The default result is the 3 largest upward and 3 largest downward episodes.

Each highlight includes the game, squad, start and end replay times, elapsed times, before and after probability, the change, prediction kind, provenance, and `seek`. `seek.t` is the replay second for `/api/matches/<id>/at/<t>`. `seek.elapsed` is the Match Explorer slider value (seconds since Playing start). This module does not register a route or a click handler.

## Coincident events

Events are read from the replay frame that carried them (`series[].t` plus `f`). That is the same replay clock as the predictions. The event's unix timestamp is not used.

| Feed `type` | Reported as |
| --- | --- |
| `down` | knock |
| `revive` | revive |
| `eliminated` | squad elimination, using `features.elimination_team` |
| `respawn` | respawn |
| `damage` | a count, not one row per packet |

A player `kill` is not listed. An elimination with no single `teamId` is unattributed. An event is `squad` when the selected squad is the actor or the victim, and `opponent` otherwise. Damage counts say how many packets were by the selected squad, against it, or between other squads.

The description lists those events as coincident. It does not say they caused the estimate change. An opponent elimination is described as something that can move the estimate with no recorded action by the selected squad.

## Call

```python
from turning_points import highlights_for_game, detect_turning_points

result = highlights_for_game(data_dir, game_id, "held_out", squad_id=None)
# result["available"] is false when the frozen model file or the held-out
# bundle is missing. result["up"] and result["down"] are then empty.
# When available, each highlight's seek.t is the replay second to open.
```

`detect_turning_points(predictions, events)` is the pure function. A prediction row is `t`, `elapsed`, `team_id`, `team_name`, `probability`, `kind`, and `provenance`.

On this checkout the frozen files `data/models/model.json` and `data/exports/heldout-session-split-v2/evaluation_model.json` are not present, so a real-game highlight list is not produced.
