# Demo guide

Apex WinLab replays an accepted Overstat match. The map shows player positions and the current ring. For every second before the result it shows each squad's win probability. Data is served from Tiger Data.

Two different counts are in play:

- **Catalog:** 100 matches on this database, read from `/api/matches` on 2026-11-14: 51 with collection `original` and 49 with collection `accepted-expansion-49`. The menu sorts by stored quality, highest first.
- **Model dataset:** the original 51 games only. The saved model in `data/models/model.json` was fit on those games. The frozen test set is **6** of them. Expansion games and URL imports are a forward pass of that same model. They are not held-out games and were not used to fit or score it.

Nothing was retrained for this guide. Measurements quoted below keep the date and source they were recorded with. They were not rechecked for the compact header.

## 1. Setup and launch

Run every command from the repository root. Python 3.12.3 is used here.

**Local data, all gitignored:**

- `data/` is the original 51-game directory: `collection/`, `raw/`, `models/`, and `exports/heldout-session-split-v2/`.
- The expansion games live in a second directory, also with `collection/manifest.json` and `raw/`. On this machine that path is `/Users/ansonlin/Apex-WinLab-expansion/data` (package `accepted-expansion-49`). It is not inside this repository. `--expansion-dir` is what registers it. Without that flag the menu can still list those games from Tiger, but their replay files are not registered, so the ring and the new-match features are unavailable.

**Credentials, in `.env`, which is gitignored.** Never commit, paste, or print the values. The importer and the dashboard do not print them, and connection errors are redacted.

- `DATABASE_URL` is the Tiger Data connection string.
- `GEMINI_API_KEY` is read by the Gemini SDK only when someone clicks **Analyze this situation** or **Review decision**. Loading the page does not send a review. Coaching behavior is in `docs/coach.md`, not in this guide.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt   # flask, psycopg, google-genai, anthropic
.venv/bin/python -m unittest discover -s tests -q
```

### Tiger Data backend

The command that serves this catalog:

```sh
set -a; . ./.env; set +a
.venv/bin/python app.py --data-dir data --port 5050 --backend tiger --expansion-dir /Users/ansonlin/Apex-WinLab-expansion/data
```

The original 51 are already imported. These commands rebuild rows or reimport; they are not part of starting the demo.

```sh
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --dry-run         # build rows, no database
.venv/bin/python import_tiger.py --data-dir data --game-id 123349 --check-reimport  # import, verify, reimport, compare counts
.venv/bin/python import_tiger.py --data-dir data --all                              # original data/ only
```

- `--all` reads `data/` only. It does not import the expansion directory.
- `--backend tiger` exits if the database cannot be used.
- `--backend auto` uses Tiger when `SELECT 1` succeeds and matches are imported, and otherwise falls back to files and says why.
- Model details names the active backend and why. The match header does not. The connection line does not include `DATABASE_URL`.

### Local file backend

```sh
.venv/bin/python app.py --data-dir data --port 5050 --backend files
```

Open `http://127.0.0.1:5050`. Port 5000 is taken by macOS AirPlay Receiver.

## 2. Three-minute walkthrough

Open this link. Do not rely on the first row of the match menu.

http://127.0.0.1:5050/?game=123633&team=20

Checked against the local original match record: game 123633 is **duo kval lobby 1**, session 23047, map id `mp_rr_district_mu1`, and squad id **20** is **EQT**. The verified winner is феечки винкс (team 18). Without `team=20` the page selects that winner, not EQT. The menu is sorted by quality, so the first held-out game is 123662, not 123633. **Start here** opens this match and squad at 18:47 and does not request a review. On 2026-11-15 the local replay showed both EQT players alive at 18:47 and NAGGI downed at 18:48. The same moment is `?game=123633&team=20&t=1127`, where `t` is match-relative slider seconds. The walkthrough below still starts at 00:00.

The page shows the event name, the readable map **E-District**, a **Saved** badge, and a **Held-out** badge. The squad menu shows the name EQT. The team id is in **Model details**, not in the menu. Raw map id, game id, and session id are in that dialog too. `?` buttons explain quality, a temporary preview, and why the estimate is held-out, new-match, or fitted.

| Time | Show | What to say |
| --- | --- | --- |
| 0:00–0:30 | The deep link. Menu tag `[held-out]`. Open **Model details**: backend "Tiger Data database" and the connection line. | "This match was never seen in training or model selection. Its session is in the frozen test split. The other 94 matches in the menu are not that test." |
| 0:30–1:00 | Slider at 00:00, then 09:45. | Map: positions use the Overstat replay's own map transform on the matching 2048 image. Only the current ring is drawn, with the outside shaded; future rings are never shown. The 2026-11-14 script recorded "Ring 0 · waiting 02:05" at 00:00 and "Ring 1 · closing" at 09:45. Those two strings were not rechecked after the header change. |
| 1:00–1:30 | Selected-squad roster and the **Win estimate** card at 09:45. | Roster: health, shields and status at the slider second. The card shows one percent, the rank among squads still in contention, and `at 09:45 · baseline …`, under the tag *Experimental estimate*. It does not repeat a long "Held-out prediction at…" sentence; the **Held-out** badge and its `?` button carry that. Squads already eliminated are 0%. Open **Model inputs for the selected squad** on that card for the 12 features. |
| 1:30–2:15 | A knock: slider at 18:47, then 18:48, then 18:51. | These percents are from the frozen held-out model, recorded before the compact header and not rechecked here. 18:47: both EQT players alive, EQT 44.3%. 18:48: NAGGI is downed, and EQT drops to **30.5%** in the same second. 18:51: the other squad also has a player downed, and EQT is back to 45.3%. That reflects the state, plus a known feature limitation (section 4). |
| 2:15–2:45 | Drag the slider fully right. | Banner: "Match finished at … Verified winner: феечки винкс. This is the recorded result, not a model prediction." The estimate card says no estimate is made after the result, and shows the winner at 100% and everyone else at 0% under **recorded outcome**. The earlier script recorded the finish as 18:57 (replay t 1206). That clock was not rechecked here. |
| 2:45–3:00 | **Model details**, section "Held-out evaluation · all test games". | "Scored once on six held-out games at 30-second checkpoints: log loss 2.341 vs 2.419 for the contention baseline (`config/frozen_evaluation.json`). A small, consistent edge; a first estimate, not a settled result. The 49 expansion games are not in that score." |

**Key moments.** Two compact carousels, *Win estimate rose* and *Win estimate fell*, for the squad selected in the menu. Each card is the representative window and one sentence for the most relevant recorded knock, revive, respawn, or elimination. **Details** on the card has the percentage-point change, the full episode, and the model file. Clicking the time seeks with the same path as the slider. The held-out list and the fitted list stay separate; the fitted list is the alternate list inside **Model details**.

For EQT in game 123633, `docs/turning-points.md` records the largest held-out drop as representative window 18:13–18:33 (59.6% to 35.1%), episode 18:05–18:49. NAGGI's knock at 18:48 sits in that episode and is not the selected window; the click seeks to 18:13. Those figures were not rechecked against the compact cards. The recorded-result jump is not in either list.

### Open an Overstat URL

The control is the **Overstat URL** field and **Open**. The pasted page is parsed and never fetched. API calls, when a download is actually attempted, go only to `https://overstat.gg/api/...`. Import progress and errors stay on the page.

A replay is saved only at quality **7.0 or above**, using the stored score rather than the rounded label, and only when every acceptance check passes, including a verified winner. It is stored under `data/user-imports/` and, with Tiger, as rows for that game id only. The header badge is **New match**. **Model details** keeps the longer line "New-match prediction · model trained on the original 51 games." when the game id and replay hash are both outside that training set.

Below 7.0, unknown quality, or a failed check becomes a **Temporary preview** for 24 hours when the replay can be shown. It is not a match row in Tiger. The `?` on that badge says it was not saved, and names the recorded reason when there is one (quality below 7.0, unknown quality, winner not verified, or another failed check). Previews are not in the match menu; they open from the URL flow. See [open-match.md](open-match.md).

URL parsing, save-versus-preview, and rate-limit behavior are covered by fixture tests in `tests/test_open_match.py`. Those tests do not call Overstat. The allowed URL shapes were read from Overstat's public JavaScript bundles. Neither that reading nor the fixture tests is a successful live download, and this guide does not claim one.

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

That diagram is the original 51-game pipeline. The expansion directory and URL imports are not training inputs and are not in `exports/heldout-session-split-v2/`. They are forward passes of `models/model.json`.

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
| **Largest changes in model estimate** | **Computed locally** from those same per-second tables (`turning_points.py`). Held-out and final model stay separate. Cached by game, kind, squad, and model sha256 |

So the Tiger-backed demo still needs the local `data/` directory and, for the 49 expansion games, `--expansion-dir`.

**In-memory caching (disclosed on the page, in each frame's `read.via`):**
- **Frames:** selecting a match starts one indexed Tiger query for that match's `player_frames` (about 40k rows on an original game), on its own connection in the background. The last 3 selected matches are kept in memory, and seeks are served from that copy. Until it loads (1.5–5 s in the 2026-11-14 note), each seek is one Tiger query, about 80 ms on that machine and almost all network round trip.
- **Per-second prediction tables:** computed once per match and model, then kept in memory. Highlight lists reuse those tables and are cached again by game, prediction kind, squad, and model sha256.
- **Measured on 2026-11-14**, one warmed match, one machine, one network, and not rechecked since: a seek went from a median of 173 ms to 11 ms. Settling after a fast scrub went from 2.8 s to 11 ms.

## 4. Metrics and limitations (audited, frozen)

These figures are the frozen audit of the original 51 games (`config/frozen_evaluation.json`, `docs/model-pipeline.md`, `docs/ring-features-v2.md`). They are not a new measurement of the 100-match catalog, and they were not recomputed for this guide.

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
The model did react: 44.3% to 30.5% in the knock second. Three seconds later the other squad also had a player downed, so both squads were one alive and one downed. Those percents are the same pre-header record as section 2, not a new check. A known feature flaw also counts a downed player's bleed-out health as health, which flatters both squads here. We documented it rather than tune against this example.

**What does Tiger Data actually do here?**
It stores the imported matches, per-second player rows in a TimescaleDB hypertable, prediction rows with provenance, and the frozen evaluation report. The 2026-11-14 original-import note recorded 3,312,521 `player_frames` rows for the 51 games (`docs/tiger-data.md`). That count was not remeasured after the expansion games were added. The dashboard reads matches, frames, checkpoints and the report from Tiger. The import verifies counts and sampled rows against the source, and reimports are idempotent. For speed, the selected match's frames are loaded with one indexed query and served from memory. The ring, map and per-second predictions still come from local files.

**Does positioning matter?**
Not measurably with this data. Neither the v1 position features nor ring-relative v2 features showed a reliable improvement; the intervals include zero. More test sessions would be needed to say either way.

## 6. Before the demo

- Copy `data/` and the expansion directory onto the machine. Both are gitignored.
- Create `.env` with `DATABASE_URL`. Add `GEMINI_API_KEY` only if the coach will be clicked. Do not print either value.
- Start the server with the Tiger command in section 1, including `--expansion-dir`.
- Open `http://127.0.0.1:5050/?game=123633&team=20` and confirm the squad menu says EQT, the badges say Saved and Held-out, and Model details names Tiger Data without showing the connection string.
- Rehearse once on the demo network. Seek time before the frame cache loads depends on that network. The 2026-11-14 timings in section 3 are not a promise for the demo machine.
- Run `.venv/bin/python -m unittest discover -s tests -q` after any code change. Do not treat a printed test total in this guide as current.
