# Dashboard

Flask view of one accepted match. It reads the collector's data directory and does not train a model or change the decoder.

## Data contract

Needed to show a match:

| Artifact | Used for |
| --- | --- |
| `DATA/collection/manifest.json` | Event, map, unrounded `quality_score`, verified winner label |
| `DATA/raw/<livedata>.timeseries.json` | Positions, health, shields, status. Path is the record's `download_path`, then `DATA/raw/` |
| Decoder `apply_frame` / `export_players` | One replay, cached under `DATA/dashboard/<game_id>.json` |

Prediction panels. Each says **Not available** when its artifact is missing or invalid; nothing is fabricated.

| Panel | Artifact | Meaning |
| --- | --- | --- |
| **Held-out win probability** (primary) | `DATA/exports/heldout-session-split-v2/` | Per-checkpoint predictions for the 6 test games of the frozen session split. The model was fit on other sessions. |
| **Final model: fitted on these games** | `DATA/models/model.json` | A forward pass of the model fit on all 51 games. In-sample; not evidence. |
| **Held-out evaluation: all test games** | `DATA/models/report.json` | Aggregate test metrics. Contention uniform is listed as the primary baseline. |

**Held-out bundle validation.** The bundle is used only if all of these hold:
- `schema_version` is 1;
- `prediction_type` is `held_out_session_split_test`;
- `held_out` is true and `final_model_predictions_included` is false;
- every file listed in `bundle.json` matches its sha256.

Predictions are joined on `(game_id, t)` and `teamId`. Each bundle checkpoint must be one of the dashboard's own checkpoints, with the same `elapsed` and the same set of squads. Any mismatch makes the panel Not available. For the 45 train and validation games, the panel says held-out predictions are unavailable and names the split.

**Slider.** The slider value is seconds since Playing start. A seek requests replay second `t = playing_start + value` from `/api/matches/<id>/at/<t>`, which returns the frame at `t`, or the latest one before it. Array indexes are never used as times. Bounds are set once per match, from its first to its last stored replay second. Frames run past Playing to the end of the telemetry. From the first `WinnerDetermined` second (`conclusion_t`), frames are `finished`. The page then shows "Match finished" and the verified winner as a recorded result, not a prediction. A replay with no `WinnerDetermined` says "Replay ends before match conclusion" at its last frame. Each seek or match selection takes a new token, and the page fetches the frame and ring together. A response for an older token is dropped. A failed ring request leaves the frame shown with "Ring: not available".

These post-outcome frames are for viewing only. Checkpoints stop at the first second that is not Playing, as before. Training uses `features.py`, which has its own cut-off and does not read this timeline.

**Ring.** In most replays, the match ends while the final stage is still closing, so that stage has no `end` or `endCenter`. From its start, the ring line says it is closing with no recorded end, and no circle is drawn. Before this was handled, every later second returned HTTP 500, so the far end of the slider could not be shown.

**Per-second predictions.** `live_predictions.py` makes a prediction at every replay second from Playing start up to the feature cut-off. The cut-off is the first second that is not Playing, or that reports `teamsAlive <= 1`. Each prediction is made the way the evaluation made its 30-second ones:
- training feature code, `features.replay_checkpoints(step=1)` and `squad_rows`, run on the local replay;
- the frozen model's own weights and scaler: the bundle's `evaluation_model.json` for the 6 test games (sha256 checked against `bundle.json`), and `models/model.json` for the in-sample final-model panel;
- `train_model.predict` / `masked` / `contention_baseline`.

Eligibility uses `features.contention` without the winner, so only elimination events and `teamsAlive` up to that second count. At all 238 held-out and 2,045 final-model 30-second checkpoints, the per-second values equal the frozen ones exactly. Leaving out the winner check changes no checkpoint's eligibility. Nothing is refit, retuned, or interpolated.

`/api/matches/<id>/at/<t>` returns the frame, its ring, and the predictions made at that same second. The panel shows them with that timestamp. After the cut-off no prediction is made. From `WinnerDetermined` on, the panel shows the recorded outcome (verified winner 100%, others 0%), labeled as a result, not a prediction. The frozen 30-second checkpoints stay available as a separately labeled history. The evaluation report is still scored only at those 30-second checkpoints, and says so.

**Known feature limitation (frozen model, not changed).** `hp_frac` sums health and shields over alive and downed players. The replay reports a downed player's health as the bleed-out pool, often 100/100, so a squad with a downed player looks healthier than its fighting strength. Example: game 123633 at 18:51, EQT (one alive, one downed). Correcting this needs a new feature and a refit, so it is left as is. The page notes it under the model inputs.

**Seek performance and caching.** Each seek is one request. At most one frame request runs at a time; slider input while it runs only replaces the pending value. Match changes abort the request in flight. The selected time shows at once, and a separate label names the frame actually displayed. With `--backend tiger`, selecting a match loads that match's `player_frames` from Tiger Data in one indexed query, in the background on its own connection. The last three selected matches are kept in memory, and seeks are served from that copy. Until it loads, each seek is one Tiger query (about 80 ms here, mostly network round trip). Every frame response says which path served it (`read.via`). Per-second prediction tables are also held in memory per match.

**Time.** The frozen evaluation's checkpoints exist every 30 s from Playing start until the replay reports `teamsAlive <= 1`. For each slider second, the server returns the most recent checkpoint at or before it (`frames/<i>` → `checkpoints.heldout` / `checkpoints.final`, with `age_s`). The page shows that checkpoint's own time (MM:SS, replay `t`, UTC on the replay clock), plus how far the slider is past it. It never selects a later checkpoint and never interpolates. The per-squad history lists only checkpoints up to the current one.

**Baseline.** Both prediction panels use contention uniform: 1 / squads without a verified elimination event, and 0 for eliminated squads. Eliminations come from feed `eliminated` events up to `t`, checked against `teamsAlive`. Where that check fails, the panel falls back to lobby uniform and says so.

The demo opens on the first held-out test game.

The plot is XY only; `z` is omitted. On a map version with a cached 2048 image, positions use the Overstat `mapCord` transform on that image and the current ring is drawn with the outside shaded (see `docs/map-rendering.md`). Any other map shows "Map background unavailable" and nothing is drawn on another map's image. The ring line names the slider time it is drawn at; prediction panels name their own, older checkpoint time.

## Data backends

`--backend tiger` (or `auto` with imported matches) reads matches, squads, player frames, both prediction kinds, and the evaluation report from Tiger Data (`docs/tiger-data.md`). Not every component is database-backed:

| Component | Source under every backend |
| --- | --- |
| Map config and image | `map_config.py` and `static/maps/*.webp` |
| Current ring (`/api/matches/<id>/ring`) | The local replay payload at the manifest's `download_path`, read by `map_config.load_rings` (`DATA/collection/manifest.json` is required) |

So the Tiger-backed dashboard still needs the local data directory for the ring.

Quality on the selector is the manifest's unrounded per-game score. It is not a model feature.

## Launch

From the repository root, with this machine's telemetry:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --data-dir data --port 5050
```

On a Mac, pass the directory that contains `collection/` and `raw/`:

```sh
.venv/bin/python app.py --data-dir /path/to/data --port 5050
```

Open `http://127.0.0.1:5050`. Port 5000 is taken by macOS AirPlay Receiver on this Mac.

## Integration checklist

- [ ] `collection/manifest.json` lists the match as `acceptance_status: accepted`.
- [ ] The raw timeseries is readable from `download_path` or `data/raw/<livedata_id>.timeseries.json`.
- [ ] The first page loads that match, its squads, and a time slider in MM:SS from Playing start.
- [ ] Moving the slider changes positions without a new full replay (the timeline is cached).
- [ ] Health, shields, and status for the selected squad use `unknown` when the replay has no value.
- [ ] Held-out panel is primary on the 6 test games, says Not available for the other 45, and shows the past checkpoint's own time.
- [ ] Final-model panel says Not available unless `models/model.json` exists, and is labeled fitted on these games.
- [ ] The evaluation panel says Not available unless `models/report.json` has `evaluation.status == "ok"`.
- [ ] `overstat_patch.py` and the frozen evaluation (`config/frozen_evaluation.json`) are unchanged by dashboard commits.

## Tests

```sh
.venv/bin/python -m unittest tests.test_dashboard -q
```
