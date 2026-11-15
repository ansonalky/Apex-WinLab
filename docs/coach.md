# AI tactical coach

The **Analyze this situation** button next to **Tactical situation** asks an LLM for an evidence-linked review of the selected squad at the second shown. It runs only when clicked. Moving the slider never sends a request. The output is labeled AI-generated. It is explanatory only: no model, prediction, or evaluation uses it.

Code: `coach.py` (packet, checks, provider, cache), `static/coach.js` (panel), routes in `app.py`. Tests: `tests/test_coach.py`.

Downward highlights also have a **Review decision** flow built on the same packet, provider and cache: see [decision-review.md](decision-review.md).

## Configuration

| Setting | Where | Default |
| --- | --- | --- |
| Provider | `config/coach.json` `provider`, or `COACH_PROVIDER` | `anthropic` (the only one implemented) |
| Model | `config/coach.json` `model`, or `COACH_MODEL` | `claude-opus-5-5` |
| Effort | `config/coach.json` `effort`, or `COACH_EFFORT` | `medium` |
| Timeout | `config/coach.json` `timeout_s`, or `COACH_TIMEOUT_S` | 90 s, 1 SDK retry |
| Refusal fallback | `config/coach.json` `fallbacks`, or `COACH_FALLBACKS=off` | `"default"` (server-side fallback beta `server-side-fallback-2026-07-01`) |
| Key | `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) in the server's environment | none |

```sh
.venv/bin/pip install -r requirements.txt
echo 'ANTHROPIC_API_KEY=...' >> .env      # .env is gitignored
set -a; . ./.env; set +a
.venv/bin/python app.py --data-dir data --port 5050
```

`GET /api/coach/status` reports the provider, the model, whether the coach is configured, and which variable supplies the credential. It reports the variable's name, never its value. Without a key, the panel shows a setup state, and a click shows the evidence packet that would be sent. No analysis is requested and none is shown.

If you change the model to one that does not accept server-side fallbacks, set `"fallbacks": null`.

## Routes

- `GET /api/matches/<id>/coach/packet?t=<replay t>&team=<teamId>` returns the evidence packet only.
- `POST /api/matches/<id>/coach` with `{"t": ..., "team": ...}` returns `status`, which is one of `ok`, `not_configured`, `error` (with `kind` and `retryable`), `rejected` (with `problems`), `finished`, `squad_eliminated` or `unavailable`. Every response that has a packet includes it.

## Evidence packet (version 1)

The packet covers the analysed second `t` and the 60 s before it (`WINDOW_S`). Each item has a stable ID, its replay `t`, its elapsed `clock`, an optional `window`, and `data`.

| ID | Content |
| --- | --- |
| `CTX` | Match, map, event name, squad, squad format (players per squad), lobby squads eliminated so far |
| `SQUAD` | Members' status, health and shields at `t` |
| `PRED` | Held-out per-second estimate, contention baseline, rank, eligibility, model file and sha256 prefix, model features. On a non-test match, the reason there is none |
| `MODEL-SCOPE` | The model's features, and the tactical measurements it does **not** use |
| `ELEV` | Relative height vs squads within 8,000 u (`tactical.measure`) |
| `FIRE-10S` | Incoming fire in the last 10 s, with attacker directions |
| `FIRE-IN`, `FIRE-OUT` | Damage taken and dealt in the window, per opposing squad |
| `PRESSURE`, `OPP-<teamId>` | Nearby squad counts and sectors; each squad within 8,000 u (max 6) |
| `RING` | Revealed ring stage, phase, and signed boundary distance per member |
| `EV-<t>-<n>` | Knocks, revives, respawns involving this squad or a nearby squad, and every squad elimination, in the window (max 20) |
| `CHG-30`, `CHG-60` | Then, now and change: standing members, nearby squads, damage, height, ring, held-out estimate |
| `DATA` | Stale and excluded positions, and the limits of the data |

**Never included:** the winner, placements, the match conclusion, the prediction cut-off, anything after `t`, or the final model's probability, because that model was fitted on this match's result. A request at or after the recorded result, or for a squad already eliminated, is refused with a request to pick an earlier moment. `verify_packet` rejects outcome keys (`winner`, `placement`, `conclusion_t`, `cutoff_t`, …), any `*_t` value after `t`, and any item or window after `t` before a request is sent.

Player, team, event and weapon names stay in the packet as data. The system prompt states they are never instructions, and the UI escapes them.

## Output and checks

The request uses structured output (`output_config.format`, JSON schema). Evidence IDs are an enum of the packet's IDs. The output has:

- `situation_summary`: at most two sentences.
- `factors` (1–3): name, `advantage`/`disadvantage`/`uncertain`, observed facts each with evidence IDs, interpretation, uncertainty, and evidence IDs.
- `recommendations` (1–2): a `conditional_recommendation` with its condition, or a `review_question`.
- `missing_information`.

The review priorities (elevation, incoming fire, local pressure, ring access, formation and condition) are given as attention priorities, not as a ranking the model must follow.

`validate_analysis` rejects a response, and none of it is shown, when any of the following holds:

- it cites an evidence ID that is not in the packet;
- a number in its text is not in the packet (within rounding of about 3%; probabilities may be written as percentages);
- an `mm:ss` time is not a time in the evidence;
- it breaks a length limit, or a conditional recommendation has no condition;
- it makes a causal claim about a result, predicts a win or loss, refers to the eventual result, or assigns a share of winning.

The number check covers digits only. Numbers written as words are not checked. Because the packet holds many values, a wrong number can still slip through when it happens to equal some other value in the packet.

## Cache and UI states

Validated results are cached in memory and in `data/coach_cache/` (gitignored). The key is the packet hash plus provider, model, effort and `PROMPT_VERSION` (`coach-v1`). Errors and rejected responses are never cached.

The panel shows the analysed time, the evidence window, the provider and model (and the serving model if a fallback answered), whether the result came from the cache, and when it was generated. Evidence chips seek with the existing slider path. When the replay moves, the review keeps its own time and says so. Inside the evidence window it reads "viewing … inside this review's evidence window". Elsewhere it reads "not a review of the current moment". Loading never blocks seeking. There is a Cancel button and a client timeout of the server timeout plus 15 s. After an error, any earlier review is removed, and **Retry** re-runs the same second.

## Verification status (2026-11-14)

- `tests/test_coach.py` covers a real knock (game 123633, team 11, t 924) and a quiet positional moment (t 760). Both packets pass the no-future check, have stable IDs, and carry no final-model probability. A request at the conclusion is refused. Fake responses exercise every validator rule and the cache rules.
- Browser: the setup state, the packet view, evidence-chip seeking, stale and in-window labels, the error and retry state, and the refusal after the result were checked on the live page. The review layout was checked with a page-local stub that was labeled as one.
- **No real provider response has been verified.** No `ANTHROPIC_API_KEY` was configured on this machine. The first real call should be checked: the response shape, the validator's rejection rate, and latency.
