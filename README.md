# Because this project is planned to be expand more in the future, model-pipelin is the main for SanD Hackathon
# Apex WinLab

Apex WinLab turns recorded professional and competitive Apex Legends match telemetry into a synchronized replay, experimental squad win estimates, and evidence-grounded coaching. It is a historical match explorer and review tool, not a live game assistant.

## Main features

- Replay player positions, facing, health/shields and squad status on a zoomable, pannable map.
- Seek through a match with an event timeline for knocks, kills, deaths, squad eliminations and recorded ring transitions.
- Display the current safe boundary and revealed ring destinations. Optional **Observer preview** can show recorded future geometry; that geometry is display-only and is excluded from model and coach inputs.
- Compare squads' win estimates, with held-out, fitted and new-match provenance kept distinct.
- **Analyze this situation** reviews the displayed squad and second. **Review a key moment** keeps the selected match/squad, finds its largest detected downward episode and opens a review at the suggested pre-episode decision time.
- A unified AI Coach panel offers **Current situation** and **Key decision** modes, cached results, one active client review, a loading timer, Cancel and Retry. Decision recommendations use only evidence available by the decision time; later events are labeled retrospective.
- Explore recorded team/player results on the Performance page. The catalog is a collected sample, not a global ranking.

## Architecture and technologies

| Layer | Implementation |
| --- | --- |
| Replay reconstruction | Python decoder and validation in `overstat_patch.py`, `collection.py` and `dashboard_data.py`; local Overstat replay payloads |
| Prediction | Standard-library Python feature extraction and conditional-logit winner model; saved model/scaler and frozen held-out exports |
| Server | Flask JSON API and HTML templates in `app.py`; local-files or Tiger Data backend |
| Database | Tiger Data (PostgreSQL/Timescale), `psycopg`, parameterized queries; schema in `tiger_schema.sql` |
| Browser | Vanilla JavaScript, HTML/CSS and Canvas 2D; shared replay seeking, map transforms and causal ring readers |
| Coaching | Google GenAI SDK/Gemini by default; optional Anthropic provider; validated evidence packets, grounding checks and local result cache |

The database supplies stored frames, estimates and result rows. Maps and ring reconstruction still require the local map assets and replay files, including when using Tiger. Loading a page does not request AI coaching. Explicit coaching actions may make a paid provider request on a cache miss; client cancellation does not guarantee provider work or billing stops.

## Setup

Use Python 3.12 (the documented demo environment uses 3.12.3). Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally. These are placeholders only:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
```

`DATABASE_URL` is required for `--backend tiger`; it must point to a database already populated with the project's tables and rows. `GEMINI_API_KEY` is optional for replay and prediction; coaching shows a setup state without it. `GOOGLE_API_KEY` is a supported Gemini alternative. Optional coach overrides are `COACH_PROVIDER`, `COACH_MODEL`, `COACH_EFFORT` and `COACH_TIMEOUT_S`; see [coach configuration](docs/coach.md). Never commit `.env` or real connection strings/keys. Flask does not automatically load this file in this setup: source it into the launch environment below.

### Required local artifacts (not in Git)

A source checkout alone is insufficient to reproduce the full demo. Obtain the existing data/model package from the project owner; keep its paths and hashes intact rather than retraining or replacing frozen artifacts.

| Local path | Required content |
| --- | --- |
| `data/collection/manifest.json` and `data/collection/cache/` | Accepted original-game manifest and cached session standings, needed for verified labels/results |
| `data/raw/` | Original replay timeseries payloads referenced by the manifest |
| `data/models/model.json` | Saved final model, feature settings and scaler for fitted/new-match inference |
| `data/models/report.json` | Original evaluation report |
| `data/exports/heldout-session-split-v2/` | Complete validated bundle: `bundle.json`, `heldout_predictions.json`, `evaluation_model.json`, `metrics.json` and its README |
| Separate expansion directory | `collection/manifest.json`, cached standings and `raw/` for the accepted expansion games |
| Existing Tiger database | Imported matches, squads, frames, estimates, evaluation reports and results for the Tiger-backed demo/Performance page |

`data/` is gitignored. Local coach caches/runs and generated replay caches are also under that tree. The frozen evaluation specification **is** tracked at `config/frozen_evaluation.json`; map images and attribution **are** tracked under `static/maps/`. Collection, database imports, training and export scripts are separate maintenance operations, not startup steps. See [model artifacts/evaluation](docs/model-pipeline.md) and [Tiger setup/schema](docs/tiger-data.md).

### Exact demo launch command

With the artifacts and already-populated Tiger database above, the documented working command on the demo machine is:

```sh
set -a
. ./.env
set +a
.venv/bin/python app.py --data-dir data --port 5050 --backend tiger --expansion-dir /Users/ansonlin/Apex-WinLab-expansion/data
```

On another machine, replace only the expansion directory with the supplied package's absolute path. The server defaults to loopback (`127.0.0.1`); no public exposure or unfinished remote-access configuration is needed. Open [Match Explorer](http://127.0.0.1:5050/) and inspect Model details to verify the active backend. `/api/backend` reports the backend; `/api/coach/status` reports configuration without requesting a review.

For an original-data-only local replay without Tiger, use:

```sh
.venv/bin/python app.py --data-dir data --port 5050 --backend files
```

This still requires the local replay/model artifacts. It does not reproduce the full Tiger catalog or database-backed Performance page. Missing artifacts are reported as unavailable; they are not fabricated. The Tiger command fails if its database cannot be used instead of silently claiming database-backed operation.

## Dataset, evaluation and limitations

- **Catalog:** the documented demo catalog contains 100 accepted matches: 51 original games plus 49 accepted expansion games. Catalog size is not the test-set size and may vary with the installed package/database.
- **Frozen evaluation:** the original 51-game dataset spans 22 audited sessions, split by session into 36 training, 9 validation and **6 test games** (238 test checkpoints). `config/frozen_evaluation.json` fixes the split, settings, data hashes and original metrics. The full catalog is not held-out evaluation.
- **Held-out estimates:** the frozen evaluation model was fit on train/validation sessions, excluding the test sessions. The original frozen raw-model game-weighted test log loss is 2.345 versus 2.419 for uniform over squads still in contention. These are small-sample experimental results, not a claim of calibrated probabilities or broad generalization.
- **Fitted estimates:** the final model was fit on all original games. Its estimates on those games are in-sample and are not independent performance evidence. The display's verified-elimination constraint is documented separately from the original raw-model evaluation.
- **New-match estimates:** expansion/opened games use a forward pass of the same saved model; they are not added to the frozen evaluation or silently used to refit it. Training membership is checked before describing them as unseen.
- Telemetry can omit ring geometry/completion times, positions and events. Unknown values remain unavailable. A suggested decision time is a review aid, not a proven mistake time. Retrospective association does not establish cause, and unplayed tactical options have no validated counterfactual win probability.
- Six test games are insufficient to establish calibration. The condition-only model performed similarly to the full model, so this evaluation does not establish a positional-feature advantage. Coaching may abstain when evidence is insufficient and does not establish that a route has cover or is traversable.

See the [evaluation audit](docs/model-pipeline.md), [decision evidence rules](docs/decision-review.md), [map/ring handling](docs/map-rendering.md) and [demo walkthrough](docs/demo.md) for detail. This README describes the current key-moment action; older walkthrough sections may refer to earlier demo controls.

## Checks

With dependencies and local artifacts installed:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

Frontend regression suites require Node.js and use mocked responses, without paid AI calls:

```sh
for test in tests/*_regression.cjs; do node "$test" || exit 1; done
```

`tests/fixture_server.py` is a separate, prominently labeled **TEST PREVIEW — NO LIVE PROVIDER** server for canned loading/error UI checks. It is not the demo launch command and refuses the real dashboard ports. Unit/fixture results do not substitute for a live-data walkthrough.

The [submission verification record](docs/submission.md) identifies the audited baseline, final checks and remaining handoff requirements.

## Demo video

No demo-video URL is recorded in this repository yet. Add the final submission link when available; the written [demo guide](docs/demo.md) is available now.
