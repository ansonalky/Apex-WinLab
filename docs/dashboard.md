# Dashboard

Flask view of one accepted match. It reads the collector's data directory and does not train a model or change the decoder.

## Data contract

Needed to show a match:

| Artifact | Used for |
| --- | --- |
| `DATA/collection/manifest.json` | Event, map, unrounded `quality_score`, verified winner label |
| `DATA/raw/<livedata>.timeseries.json` | Positions, health, shields, status. Path is the record's `download_path`, then `DATA/raw/` |
| Decoder `apply_frame` / `export_players` | One replay, cached under `DATA/dashboard/<game_id>.json` |

Needed for probabilities and the evaluation panel, and **not in this data directory**:

| Artifact | What it would show |
| --- | --- |
| `DATA/models/model.json` | Final-model fitted win probabilities at 30-second checkpoints, via `train_model.predict`. Not held-out. |
| `DATA/models/report.json` | Held-out aggregate log loss, Brier, top-1, and calibration. Per-checkpoint held-out probabilities are not stored, so they cannot be plotted. |
| `DATA/models/extraction.json` | Extraction inventory only. The dashboard does not require it. |

`docs/model-pipeline.md` describes a completed evaluation. Those figures are not copied into the UI. Until `report.json` and `model.json` are present, both panels say **Not available**.

Coordinate units are unverified. The plot is XY only. `z` is omitted. No map image is used.

Quality on the selector is the manifest's unrounded per-game score. It is not a model feature.

## Launch

From the repository root, with this machine's telemetry:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --data-dir /workspace/data --port 5000
```

On a Mac, pass the directory that contains `collection/` and `raw/`:

```sh
.venv/bin/python app.py --data-dir /path/to/data --port 5000
```

Open `http://127.0.0.1:5000`.

## Integration checklist

- [ ] `collection/manifest.json` lists the match as `acceptance_status: accepted`.
- [ ] The raw timeseries is readable from `download_path` or `data/raw/<livedata_id>.timeseries.json`.
- [ ] The first page loads that match, its squads, and a time slider in MM:SS from Playing start.
- [ ] Moving the slider changes positions without a new full replay (the timeline is cached).
- [ ] Health, shields, and status for the selected squad use `unknown` when the replay has no value.
- [ ] Win probability says Not available unless `models/model.json` exists, and any curve is labeled `final_model_fitted`, not held-out.
- [ ] The evaluation panel says Not available unless `models/report.json` has `evaluation.status == "ok"`.
- [ ] `features.py`, `train_model.py`, and `overstat_patch.py` are unchanged by dashboard commits.

## Tests

```sh
.venv/bin/python -m unittest tests.test_dashboard -q
```
