# Decision review of a downward turning point

Each **decrease** in the "Largest changes in model estimate" lists has a **Review decision** button. The button opens the **Decision review** panel, which shows two spans separately:

- the highlight's **representative window**, with the estimate before and after it;
- the **full episode** it belongs to.

Each span has its own seek chip. Nothing is requested when the panel opens.

The **decision time** is editable (mm:ss). It defaults to 10 s (`LEAD_S`) before the episode begins, and never earlier than replay start. It must be before the episode end. It is labeled a *suggested review time, not a proven mistake time*. **Review decision** seeks to the decision time and only then requests the review. The decision time stays pinned beside the slider, with a "Go to" chip, while you move elsewhere.

Code: `decision_review.py` (packet, prompt, schema, checks), `static/decision.js` (panel, pin, map marker), routes in `app.py`, and shared provider, cache and checks from `coach.py` (see [coach.md](coach.md)). Tests: `tests/test_decision_review.py`.

## Routes

- `GET /api/matches/<id>/decision-review/packet?kind=&team=&start_t=&episode_start_t=&decision_t=` returns the packet only.
- `POST /api/matches/<id>/decision-review` with the same fields as JSON. The response has the same statuses as the coach (`ok`, `not_configured`, `error`, `rejected`, `finished`, `squad_eliminated`, `invalid`, `unavailable`), plus `packet` and `suggested_t`.

The client only *names* the episode (`start_t`, `episode_start_t`, `kind`, `team`). The server looks it up again in the highlight cache. A name that does not match a downward highlight returns `unavailable`.

## Packet (prompt `decision-v1`)

**Recommendation inputs (`items`)**: everything at or before the decision time. These are the coach's packet at that second (squad condition, held-out estimate and model scope, height, fire in the last 10 s and 60 s, nearby squads, revealed ring, recorded events and 30/60 s changes, data limits), plus:

| ID | Content |
| --- | --- |
| `SEP` | Pairwise teammate distances, with each member's distance and direction from the squad centre |
| `MOVE` | Straight-line displacement of the squad centre and of each member over the last 30 s and 60 s |
| `REF-SQUAD`, `REF-MATE-n`, `REF-RING` | Positions with known coordinates: the squad centre, each teammate with a fresh position, and the revealed ring centre. A note on each says the route is not verified |
| `OBSERVER` | Which inputs are observer-only (opponent positions, pressure, elevation comparison) and which the squad could perceive (own state, damage taken, kill feed, ring) |

`decision` holds only the decision second and its label. Episode times, the suggested time and later data are kept out of this part.

**Retrospective (`retrospective.items`)**: from after the decision time to the episode end. It is used only to describe the drop.

| ID | Content |
| --- | --- |
| `R-EPISODE` | Representative window and full episode, with before/after estimate, model file and sha256. Marked in-sample for a final-model highlight |
| `R-OWN` | This squad's estimate and model features at the episode's first and last second |
| `R-OTHERS` | Up to 5 other squads with the largest estimate changes over the episode. Estimates are renormalized, so their gains can lower this squad's |
| `R-FIRE` | Damage taken and dealt after the decision time, by attacking squad |
| `R-EV-<t>-<n>` | Knocks, revives and respawns involving this squad or a squad nearby at the decision time, plus every squad elimination |

**Never included:** the winner, placements, the match conclusion, the cut-off, or the number of squads remaining after the decision time. That number could reveal placement.

`verify_decision_packet` checks the recommendation part against the decision time and the retrospective against the episode end. It also checks for outcome keys, duplicate IDs, and `squads_remaining` / `in_contention` in the retrospective.

## Output and checks

The request uses the structured output schema. Recommendation evidence enums list only pre-decision IDs. Retrospective IDs are allowed only for the drop attribution. A destination must be a `REF-*` ID or `none`.

- `drop_attribution`: `own_squad_deterioration`, `changes_elsewhere`, `both` or `insufficient_evidence`, with an explanation and evidence. This is an association, not a cause.
- `weaknesses`: 0–3, each with facts (and evidence), interpretation and uncertainty.
- `options`: 1–3 of `hold`, `regroup`, `disengage`, `rotate_to_verified_position`. Each has a destination, evidence, expected benefit, cost, uncertainty, and whether it depends on observer-only information.
- `recommendation`: one of the compared options, or `abstain`. A non-abstaining choice needs a condition and pre-decision evidence.
- `practice_takeaway` and `missing_information`.

`validate_decision` rejects the whole response, and none of it is shown, in any of these cases:

- recommendation inputs cite retrospective or unknown evidence;
- a `rotate_to_verified_position` option has no destination, or a destination has no known coordinates;
- a regroup destination is not a teammate;
- an option is compared twice, or there are more than 3 options;
- the recommendation names an option that was not compared;
- an option or the recommendation gives a probability or `%` for an unplayed alternative;
- a sentence mentions cover, sightlines, buildings or a safe route without hedging it with *if*, *unknown*, *not*, *may* and similar words (`missing_information` is exempt);
- it fails the coach's number, time and causal/outcome checks.

## UI

- **Facts, interpretation and recommendation** are styled apart. Retrospective chips are dashed. Every chip seeks with the existing slider path.
- **Recommended destination:** the map draws a dashed circle labelled "route not verified" at the destination's known coordinates (as at the decision time). It never draws a line or route, because none is known.
- **Moving the replay:** after the decision time the panel says the recommendations use evidence only up to it. Before it, the review is labeled with its own time.
- **Failures:** every AI hook in `dashboard.js` is wrapped in `safely()`. A failed request (network, HTTP 500, timeout, rejection) shows an error with **Retry** for the same second, and never shows an older review. Replay startup and seeking do not depend on it.

## Verification (2026-11-14)

- **Real episode:** game 123633, Meaningless (team 11), held-out decrease of 9.3 points. The representative window is 14:10–14:30 and the full episode 13:57–14:34. The suggested decision time is 13:47 (replay t 896). The packet holds 28 pre-decision items and 13 retrospective items, including this squad's knock at 14:15. It passes the split and no-outcome checks.
- **Browser, on the live page:** checked
  - the Review decision button and the panel opening without a request;
  - an invalid time rejected and a valid edited time sought;
  - reset, and the run seeking to 13:47 and ending at the setup state with both packet sections;
  - the pin persisting away from the decision time;
  - retrospective chip seeking;
  - an HTTP 500 shown with Retry while seeking kept working.

  The layout of a full review and the map marker were checked with a page-local stub labelled "STUB UI TEST — not a model response".
- **Real Gemini review (`gemini-4-hydrogen`, 31 s):** the attribution was own-squad deterioration, citing only `R-*` and retrospective events. It compared regroup (destination `REF-MATE-2`), hold and disengage, and its options and recommendation cited only pre-decision evidence. It recommended a conditional regroup and gave a spacing takeaway. It passed every check.

## Limitations

- **Terrain:** there is no terrain, cover, line-of-sight or navmesh data. The terrain check is a wording check, not geometry.
- **Movement:** it is straight-line displacement between samples. The path taken is unknown.
- **Decision time:** the default is a heuristic. No method here finds the moment a mistake was made.
- **Final-model highlights:** these can be reviewed, but that model was fitted on this match. Its estimates appear only in the retrospective and are labelled in-sample.
- **Number check:** as in the coach, it covers digits only and accepts any value that appears in the packet.
