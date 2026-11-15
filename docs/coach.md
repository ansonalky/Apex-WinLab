# AI tactical coach

The **Analyze this situation** button next to **Tactical situation** asks an LLM for an evidence-linked review of the selected squad at the second shown. It runs only when clicked. Moving the slider never sends a request. The output is labeled AI-generated. It is explanatory only: no model, prediction, or evaluation uses it.

Code: `coach.py` (packet, checks, provider, cache), `static/coach.js` (panel), routes in `app.py`. Tests: `tests/test_coach.py`.

Downward highlights also have a **Review decision** flow built on the same packet, provider and cache: see [decision-review.md](decision-review.md).

## Coach card layout (prompts `coach-v3` and `decision-v3`)

The AI Coach card has one heading, one *AI-generated* badge and one **Analyze this situation** button.

1. **Recommended action** comes first. In the situation review it is the first conditional recommendation, with its condition shown once and **Why**: the first sentence of the interpretation of the factor that shares the most evidence with it. In the decision review it is the chosen option (and its known destination, "route not verified"), its condition and the first sentence of the rationale. With no supportable recommendation, or an abstaining decision review, it says what to check first instead.
2. The squad and the reviewed MM:SS stay visible. The stale-review warning stays visible too, for an earlier or later time or another squad.
3. Expandable sections hold everything else:
   - situation review: the situation summary, then alternatives and review questions;
   - decision review: the retrospective drop explanation, the weaknesses, and the alternatives compared.
4. **Review details** sits beside *Tactical measurements* (and inside the decision panel). It holds the provider, model, thinking level, prompt, generation time, latency, cache status, tokens, request ID, packet hash, the evidence IDs with clickable timestamps, the factors, missing information and the packet.

Opening a section never seeks or calls the provider. Evidence chips seek through the slider path.

**Repeated condition.** Of the cached situation reviews, one `coach-v2` response put its whole `condition` at the start of `text` as well. The renderer printed `condition — text`, so the condition appeared twice. Two changes:

- From `coach-v3` / `decision-v3`, the schema descriptions and prompts say that `condition` holds only the If/When clause and `text` (or `rationale`) only the action or reason.
- For older cached responses, the display drops a leading copy of the condition from the text. Stored responses and validation are unchanged.

The prompt-version change means reviews cached under v1/v2 are not served again for new requests. They still render correctly if loaded.

Real results while checking, game 123633, Meaningless:

| Review | Result |
| --- | --- |
| Situation, 13:47 | accepted, 22 s; served from cache 0.5 s on repeat |
| Decision, 13:47, try 1 | rejected: "sightlines of additional squads" |
| Decision, 13:47, try 2 | rejected: "coordinate cover" |
| Decision, 13:47, try 3 | accepted, 38 s |

The grounding checks are unchanged.

## Configuration

The default provider is **Gemini**, through the official Google GenAI SDK (`google-genai`), with the output schema sent as `response_json_schema`. Anthropic is still implemented and can be selected explicitly. Only the configured provider is called. If it fails, the error is shown, and the coach never falls back to another provider.

`config/coach.json` holds `provider` and a block per provider under `providers`. Environment variables override it:

| Setting | Variable | Gemini default |
| --- | --- | --- |
| Provider | `COACH_PROVIDER` | `gemini` |
| Model | `COACH_MODEL` | `gemini-4-hydrogen` |
| Effort | `COACH_EFFORT` | `medium`, sent as Gemini `thinking_level` |
| Timeout | `COACH_TIMEOUT_S` | 120 s |
| Key | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), read by the SDK | none |

The Anthropic block keeps `claude-opus-5-5`, `ANTHROPIC_API_KEY` and its server-side refusal fallback. That fallback is between Claude models only, not between providers.

```sh
.venv/bin/pip install -r requirements.txt
set -a; . ./.env; set +a          # .env is gitignored; holds GEMINI_API_KEY and DATABASE_URL
.venv/bin/python app.py --data-dir data --port 5050
```

`GET /api/coach/status` reports the provider, the model, whether the coach is configured, and the *name* of the credential variable. Values are never printed or logged, and provider error text is redacted before it is shown. Results record `provider`, `model` and `provider_meta.served_model` (Gemini's `model_version`), and the cache key includes the provider and model.

### Model choice (2026-11-14)

Candidates listed by `models.list` for this key with `generateContent` were tried on the real decision-review schema and packet (game 123633, 13:47):

| Model | Result |
| --- | --- |
| `gemini-2.5-pro` | listed, but HTTP 404 for this key |
| `gemini-3.8-flash` | HTTP 504 after about 148 s, twice |
| `gemini-3.5-flash` | 16 s; one response rejected for asserting "share cover" |
| `gemini-4-hydrogen`, default thinking | 68 s, about 10.5k thinking tokens |
| `gemini-4-hydrogen`, `medium` | **30 s, 0 grounding problems, schema accepted (chosen)** |

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

## Verification status

**Real Gemini responses, 2026-11-14** (`gemini-4-hydrogen`, `medium`, game 123633, team 11). All passed the grounding checks, and every cited evidence ID exists.

| Review | Moment | Latency | Tokens in / out / thinking |
| --- | --- | --- | --- |
| Engagement, situation review | 14:15, knock | 17.8 s | 6,951 / 1,548 / 2,287 |
| Quiet, situation review | 11:31 | 28.2 s | 5,287 / 1,304 / 5,245 |
| Decision review | 13:47 | 31.1 s | 11,578 / 1,972 / 5,201 |

A repeat request was served from the cache. Earlier Anthropic-era checks, which still apply:

- the packets pass the no-future check;
- a request at the conclusion is refused;
- fake responses exercise every validator rule.

**Browser, port 5050 (Tiger Data backend), real Gemini responses, no stubs:**

- Situation review at 14:15: 17.4 s, 19 distinct evidence chips, all resolved. Seeking kept working while it loaded, a chip click sought to 13:45, and the review was then labelled "inside this review's evidence window".
- Situation review at 11:31: 18.7 s, 15 chips, all resolved.
- Decision review at 13:47:
  - The first real response was **rejected** by the cover/route check, and its practice takeaway was withheld. Retry was offered and no older review was shown.
  - The retry was accepted in 28 s. Retrospective chips appeared only in the drop attribution, and the regroup marker was drawn at Alel's known position. It stayed aligned at 2.6× zoom.

## Terrain grounding (both reviews, prompts `coach-v2` and `decision-v2`)

No packet contains terrain, cover or line-of-sight data. `coach.terrain_claims` applies one standard to the situation review and the decision review. It finds mentions of these features:

- cover;
- sightlines or line of sight;
- buildings;
- high-ground control;
- a safe, clear or protected route.

It then classifies each mention by its role in the sentence, not by banning words. A mention is **allowed** when:

- it is in a **question**;
- it is inside or after a **condition** in the same sentence ("If a covered path exists …, regroup"; "…, provided the path is safe"), or it is in a recommendation that has a condition field;
- it is in a clause that marks it **unknown, absent, denied or to be checked** ("without verified cover", "does not establish line of sight", "review whether it has cover").

Any other mention is a **claim that the feature exists**, such as "share cover" or "the route to Alel is safe", and the response is rejected. Hedges are scoped to their clause, so "Alel has cover, though the route may be exposed" is rejected for the first clause. Uncertainty, missing information and condition fields state what is unknown or assumed, so they are not checked as claims. The same rule is stated in both system prompts. Tests use the real Gemini sentences (`tests/test_grounding.py`).

Real reviews after the change (game 123633, team 11):

| Review | Result | Latency |
| --- | --- | --- |
| Quiet, 11:31 | accepted | 24.9 s |
| Decision, 13:47 | accepted | 31.1 s |
| Engagement, 14:15, first try | rejected for the time "14:12", which is not in the evidence | — |
| Engagement, 14:15, retry | accepted | 26.8 s |

Every accepted terrain mention was conditional or stated as unknown. Ring wording matched the shared labels: "Ring 2 closed · ring 3 next", "Ring 3 closed · ring 4 next" and "Ring 4 closing". The prompts tell the model to use `label`/`ring_number` and never `stage_raw`.
