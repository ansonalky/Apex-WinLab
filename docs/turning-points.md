# Largest changes in model estimate

`turning_points.py` finds substantial moves in a squad's per-second win estimate. It does not fit a model, and it does not change the frozen evaluation or the way a probability is made. The Match Explorer reads the result.

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

Changes are percentage points: `(after - before) * 100`. The default minimum is 5 points. Overlapping windows for the same squad and the same direction are one episode. The returned window is the one with the largest absolute change; a tie keeps the shorter window, then the earlier start. That window is the **representative window**. The merged run is the **full episode**, and it can be longer. The default result is the 3 largest upward and 3 largest downward episodes.

Before and after probabilities, and the percentage-point change, belong to the representative window. `episode_start_t` / `episode_end_t` and `episode_start_elapsed` / `episode_end_elapsed` are the full episode, on the same clock. `seek.t` is the representative window's start, the replay second for `/api/matches/<id>/at/<t>`. `seek.elapsed` is the Match Explorer slider value (seconds since Playing start).

Coincident events are those recorded from the representative window's start through its end. An event that falls only in the rest of the episode is not listed on that highlight.

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

## Route

`app.py` registers `GET /api/matches/<game_id>/highlights?kind=held_out|final_model_fitted&team=<teamId>`. `turning_points.py` does not import Flask.

`kind` and `team` are required. An unknown match is HTTP 404. A missing or unknown `kind`, a missing `team`, or a squad that is not in the match is JSON with `available: false`, a `missing` reason, and empty `up` and `down`. A missing model file, a missing held-out bundle, or a game outside the held-out test split uses that same shape. No probability is invented.

Results are cached in memory. The key is the game, the prediction kind, the squad, and the model version (`provenance.sha256` of the frozen file). The handler passes the dashboard's `LivePredictions` into `highlights_for_game`, so the per-second tables already built for the replay are reused. Held-out and `final_model_fitted` are different keys and different panels. A numeric squad id in the query string matches the replay's integer `teamId`.

## Dashboard

The Match Explorer shows one list under the held-out probabilities and another under the final model. Each row shows:

- the representative window and the full episode, as separate spans;
- the before and after probabilities and the percentage-point change of the representative window;
- the prediction kind, model file, and sha256;
- coincident events recorded inside the representative window.

Clicking a row sets the time slider to `seek.elapsed` and calls the same `seek()` path as dragging the slider. The map, roster, ring, and both probability panels move to that second.

When the request fails, the panel says the highlights could not be loaded. When `available` is false, it says **Not available** and shows the reason. When the model ran and nothing moved by 5 percentage points or more, it says so. The recorded-result jump is not a row in either list.

## Checked example

`data/` is gitignored. Without the frozen files the route returns `available: false` and empty lists.

Where those files are present, game 123633, squad EQT (team 20), held-out model: the largest downward episode is 18:05–18:49 (replay t 1154–1198, 46 windows). Its representative window is 18:13–18:33 (t 1162–1182), 59.6% to 35.1%, −24.5 percentage points. NAGGI's knock at 18:48 (t 1197) is inside that episode and outside that window, so that row does not list the knock, and a click seeks to 18:13 rather than 18:48. The final-model list is a separate pass over `models/model.json` and can differ. No highlight in either list reaches the recorded 100% at `WinnerDetermined` (the cut-off for this game is replay t 1206).

## Call

```python
from turning_points import highlights_for_game, detect_turning_points

result = highlights_for_game(data_dir, game_id, "held_out", squad_id=None, live=live)
# Pass the dashboard LivePredictions as live so its tables are reused.
# result["available"] is false when the frozen model file or the held-out
# bundle is missing. result["up"] and result["down"] are then empty.
# When available, each highlight's seek.elapsed is the slider value to open.
```

`detect_turning_points(predictions, events)` is the pure function. A prediction row is `t`, `elapsed`, `team_id`, `team_name`, `probability`, `kind`, and `provenance`.

Each decrease has a **Review decision** button for an AI review of the moment before it; see [decision-review.md](decision-review.md).
