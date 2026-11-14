# Demo guide

Apex WinLab replays one accepted Overstat match. The map shows player positions and the current ring. For every second before the result it shows each squad's win probability from a frozen, session-split model. Data is served from Tiger Data.

Everything here describes the code at `model-pipeline` as of 2026-11-14. The figures come from:
- `config/frozen_evaluation.json`;
- `data/exports/heldout-session-split-v2/`;
- `docs/model-pipeline.md` and `docs/ring-features-v2.md`;
- the import and browser checks recorded in `docs/tiger-data.md` and `docs/dashboard.md`.

Nothing was retrained for this guide.

## 1. Setup and launch

Run every command from the repository root. Python 3.12.3 is used here. The data directory `data/` is gitignored; the demo machine needs a copy with `collection/`, `raw/`, `models/` and `exports/heldout-session-split-v2/`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # flask, psycopg[binary]
.venv/bin/python -m unittest discover -s tests -q     # 123 tests, 1 skipped when a sample payload is absent
```

**Credentials.** Put the Tiger Data connection string in `.env` as `DATABASE_URL=...`. `.env` is gitignored. Never commit it, paste it, or show it on screen. The importer and the dashboard never print it, and connection errors are redacted.

### Tiger Data backend

```sh
set -a; . ./.env; set +a
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --dry-run         # build rows, no database
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --check-reimport  # import, verify, reimport, compare counts
.venv/bin/python import_tiger.py --data-dir data --all                              # all 51 games
.venv/bin/python app.py --data-dir data --port 5050 --backend tiger
```

- The import is already done for the current database. Rerun `--all` only after a schema change; it is idempotent.
- `--backend tiger` exits if the database cannot be used.
- `--backend auto` uses Tiger when `SELECT 1` succeeds and matches are imported, and otherwise falls back to files and says why.
- The page header always names the active backend.

### Local file backend

```sh
.venv/bin/python app.py --data-dir data --port 5050 --backend files
```

Open `http://127.0.0.1:5050`. Port 5000 is taken by macOS AirPlay Receiver.

## 2. Three-minute walkthrough

Use the default match: **duo kval lobby 1**, game 123633, session 23047, map `mp_rr_district_mu1`. It is a held-out test game, and the verified winner is феечки винкс. To show the probabilities below, choose squad **EQT (20)** in the Squad menu.

| Time | Show | What to say |
| --- | --- | --- |
| 0:00–0:30 | Header: "Data backend: Tiger Data database". Match menu tag `[held-out test]`. | "This match was never seen in training or model selection. Its session is in the frozen test split." |
| 0:30–1:00 | Slider at 00:00, then 09:45. | Map: positions use the Overstat replay's own map transform on the matching 2048 image. Ring line: at 00:00, "Ring 0 · waiting 02:05"; at 09:45, "Ring 1 · closing". Only the current ring is drawn, with the outside shaded; future rings are never shown. |
| 1:00–1:30 | Selected-squad roster; held-out panel at 09:45. | Roster: health, shields and status at the slider second. Prediction: made at that same second ("Held-out prediction at 09:45, replay t …, the same second as the map and roster"), with squads already eliminated set to 0%. Open "Model inputs" to show the 12 features the model saw. |
| 1:30–2:15 | A knock: slider at 18:47, then 18:48, then 18:51. | 18:47: both EQT players alive, EQT 44.3%. 18:48: NAGGI is downed, and EQT drops to **30.5%** in the same second. 18:51: the other squad also has a player downed, and EQT is back to 45.3%. That reflects the state, plus a known feature limitation (section 4). |
| 2:15–2:45 | Press End (19:29). | Banner: "Match finished at 18:57 (replay t 1206). Verified winner: феечки винкс." The panel shows the winner at 100% and everyone else at 0%, labeled **recorded outcome, not a model prediction**. No prediction is made after the result. |
| 2:45–3:00 | The evaluation panel. | "Scored once on six held-out games at 30-second checkpoints: log loss 2.341 vs 2.419 for the contention baseline. A small, consistent edge; a first estimate, not a settled result." |

Optional, if asked about missing maps: game 123467 (`mp_rr_district_mu1_night`) shows "Map background unavailable"; nothing is drawn on another map's image.

## 3. Architecture (as implemented)

```
Overstat public API + replay timeseries
   │ collect_matches.py: quality ≥ 7.0, replay validation, winner verified against standings
   ▼
data/collection/manifest.json + data/raw/*.timeseries.json   (51 games, sha256-pinned)
   │ overstat_patch.py: Overstat's own patchData/jsondiffpatch semantics, per second
   ▼
Reconstructed state per replay second (positions, health, shields, status, ring, feed)
   │ features.py → 12 squad features at checkpoints     train_model.py → conditional logit
   │ export_heldout.py → frozen session-split bundle (evaluation_model.json + predictions)
   ▼
import_tiger.py ──► Tiger Data (TimescaleDB)
   matches · squads · player_frames (hypertable on ts) · squad_estimates · evaluation_reports
   ▼
app.py (Flask) + static/*.js ──► dashboard
```

**What the dashboard reads from where, under `--backend tiger`:**

| Component | Source |
| --- | --- |
| Match list, squads, winner, `conclusion_t` | Tiger `matches`, `squads` |
| Positions, health, shields, status per second | Tiger `player_frames` |
| 30-second held-out and final-model checkpoints | Tiger `squad_estimates` (`held_out`, `final_model_fitted`) |
| Evaluation report | Tiger `evaluation_reports` |
| **Ring** | **Local replay files** (`map_config.load_rings`). Not in the database. |
| **Map image and transform** | **Local** `map_config.py` and `static/maps/*.webp` |
| **Per-second predictions** | **Computed locally** (`live_predictions.py`) from the local replay and the frozen model files |

So the Tiger-backed demo still needs the local `data/` directory.

**In-memory caching (disclosed on the page, in each frame's `read.via`):**
- **Frames:** selecting a match starts one indexed Tiger query for that match's `player_frames` (about 40k rows), on its own connection in the background. The last 3 selected matches are kept in memory, and seeks are served from that copy. Until it loads (1.5–5 s), each seek is one Tiger query, about 80 ms here and almost all network round trip.
- **Per-second prediction tables:** computed once per match and model, then kept in memory.
- **Measured** on a warmed match in the browser: a seek went from a median of 173 ms to 11 ms. Settling after a fast scrub went from 2.8 s to 11 ms. These figures are from one machine on one network.

## 4. Metrics and limitations (audited, frozen)

- **Data:** 51 accepted games across **22** sessions, 5 maps and 11 organizers. `HANDOFF.md` says 23 sessions; the audit in `docs/model-pipeline.md` counts 22.
- **Split by session:**

  | Split | Sessions | Games | Checkpoint groups |
  | --- | --- | --- | --- |
  | Train | 14 | 36 | |
  | Validation | 4 | 9 | |
  | Test | 4 | **6** | 238 |

  `l2 = 0.01` was chosen on validation. Test was scored once and then frozen.

- **Test results**, game-weighted:

  | Method | Log loss | Brier | Top-1 |
  | --- | --- | --- | --- |
  | Model, eligibility-constrained (shown in the dashboard) | **2.341** | 0.880 | 0.147 |
  | Model, raw | 2.345 | 0.881 | 0.147 |
  | Uniform over squads still in contention | 2.419 | 0.895 | 0.105 |
  | Most standing players | 2.637 | 0.876 | 0.126 |
  | Uniform over the lobby | 2.850 | 0.942 | 0.058 |

- **The improvement is modest.** The constrained model is 0.078 nats better than contention-uniform. It is better in all 6 test games, by 0.017 to 0.129 nats. Most of the gain over lobby-uniform comes from knowing which squads are out, not from the model. Six games from four sessions is a small test set. No confidence interval was computed on test, and calibration is not reported (the minimum is 20 games).
- **Positional features show no demonstrated improvement:**
  - v1 position features: test log loss 2.345 vs 2.343 for condition-only; better in 3 games, worse in 3.
  - v2 ring-relative features (train and validation only, test never used): the 95% bootstrap intervals include zero.
- **Downed-health limitation:** `hp_frac` sums health and shields over alive *and downed* players. The replay reports a downed player's bleed-out health, often 100/100, so a squad with a downed player looks healthier than it is. At 18:51 in game 123633, EQT's `hp_frac` is 0.61, or 0.72 counting only alive players. The other squad's is 0.75, or 1.00. Fixing this needs a new feature and a refit, so the frozen model keeps it.
- **Per-second inference vs 30-second evaluation:**
  - The dashboard predicts at every second up to the cut-off, with the training feature code and the frozen models.
  - At all 238 held-out and 2,045 final-model 30-second checkpoints, the per-second values equal the frozen ones exactly.
  - **The evaluation itself covers only the 30-second checkpoints.** Accuracy at other seconds has not been measured separately.
  - Per-second eligibility leaves out one check the 30-second evaluation used (the eventual winner marked eliminated), because that check needs the outcome. Omitting it changes no evaluated checkpoint.
- **Other limitations:**
  - Coordinate units are not verified; only XY is used.
  - In most replays the final ring stage has no recorded end, so it is not drawn.
  - The "fitted on these games" panel is in-sample and is not evidence.
  - Probabilities are associations, not causal effects of decisions.

## 5. Likely judge questions

**Does the model see the future?**
No. Features come from the reconstructed state at or before each second, and eligibility uses only elimination events and `teamsAlive` up to that second. Checkpoints stop when the game leaves Playing or `teamsAlive <= 1`. Frames after the result are for viewing only and never become training rows. The winner label is used only for scoring.

**Is it better than a simple baseline?**
Slightly. Log loss is 2.341 vs 2.419 for an equal split among squads still in contention. That is better in all six held-out games, but six games is a small sample and calibration is not reported. Against an equal split over the whole lobby (2.850) the gap looks large, but most of that comes from knowing who is eliminated.

**Why did EQT stay near 50% after NAGGI went down?**
The model did react: 44.3% to 30.5% in the knock second. Three seconds later the other squad also had a player downed, so both squads were one alive and one downed. A known feature flaw also counts a downed player's bleed-out health as health, which flatters both squads here. We documented it rather than tune against this example.

**What does Tiger Data actually do here?**
It stores the imported matches, 3.3 M per-second player rows in a TimescaleDB hypertable, both prediction kinds with provenance, and the frozen evaluation report. The dashboard reads matches, frames, checkpoints and the report from it. The import verifies counts and sampled rows against the source, and reimports are idempotent. For speed, the selected match's frames are loaded with one indexed query and served from memory. The ring, map and per-second predictions still come from local files.

**Does positioning matter?**
Not measurably with this data. Neither the v1 position features nor ring-relative v2 features showed a reliable improvement; the intervals include zero. More test sessions would be needed to say either way.

## 6. Before submission

- [ ] Stats tab (Cursor): merge it, then rerun `.venv/bin/python -m unittest discover -s tests -q` and recheck the walkthrough in the browser.
- [ ] Open a PR from `model-pipeline` to `main` and merge it.
- [ ] Demo machine: copy `data/` (gitignored), create `.env` with `DATABASE_URL`, and confirm the header says "Tiger Data database (SELECT 1 ok; 51 imported matches)".
- [ ] Rehearse the walkthrough once on the demo network. Seek speed before the cache loads depends on network round-trip time.
- [ ] Add `.DS_Store` to `.gitignore` (currently untracked).
- [ ] Decide whether to fix the session count in `HANDOFF.md` (23 → 22).
- [ ] Optional: present calibration as not reported (6 of the required 20 games) rather than omitting it.
