# Tactical situation (explanatory)

`tactical.py` measures the selected squad's situation at one replay second, and the dashboard shows it under **Tactical situation**. It explains the state. It is **not a model input**: the frozen models, the per-second predictions and the evaluation do not use it, and no weight was changed. Whether these measurements capture the situations that matter is still to be reviewed before any model experiment.

The **Analyze this situation** button sends these measurements, with recent events, to an LLM for a review on request; see [coach.md](coach.md).

## Source and timing

- **One causal pass per match** over the local replay, with the dashboard's decoder (`apply_frame`, `export_players`). At second `t`, only state applied up to `t` is used.
- **Feed events:** only those that arrived at or before `t`.
- **Ring:** `ring_features.ring_at` over live ring entries, meaning stages already inserted when they started closing. The planned schedule at `t = 0` and future stage centers are never used.
- **Caching:** the last 2 matches are kept in memory, and building one takes about 0.3 s.
- **Backend:** under `--backend tiger` this still reads the local replay, like the ring and the per-second predictions.

## Units and thresholds

Coordinates are **raw replay units (u)**. Their real-world scale has not been verified. For orientation only: on the four cached maps, the image edge lies 42,500–51,000 u from the map origin (`map_config.MAP_SIZE`), so each map is roughly 85,000–102,000 u across.

| Setting | Value | Meaning |
| --- | --- | --- |
| `FRESH_GAP_S` | 5 s | A position updated longer ago is stale and not used |
| `NEAR_UNITS` | 3,000 u | "Near" opposing squad, by centroid distance |
| `MEDIUM_UNITS` | 8,000 u | "Medium" band; also the elevation comparison distance |
| `FIRE_WINDOW_S` | 10 s | Incoming-fire window, `t − 10 < event ≤ t` |
| `MIN_BEARING_UNITS` | 500 u | Below this, no bearing is reported between squads |

Bearings are on the map image: north is up (replay +y), east is right (replay +x), measured in degrees clockwise from north, and reported in 8 sectors.

## Contract: `GET /api/matches/<id>/tactical?t=<replay t>&team=<teamId>` (version 1)

| Field | Content |
| --- | --- |
| `t`, `elapsed`, `team` | The measured second (clamped to stored seconds) and squad |
| `explanatory_only`, `limits` | Always `true`, plus the interpretation limits below |
| `quality` | Per member: `status`, `position_used`, `pos_age_s`, `z_age_s`, and a note when the position is an eliminated or dead player's last known position, or is stale. Counts of standing members with fresh positions, and of opposing standing players used and excluded as stale |
| `elevation` | `squad_z` (mean fresh z of standing members), opposing squads compared within 8,000 u, `higher_than`, `lower_than`, `mean_dz` (positive: this squad is higher) |
| `fire` | Damage events to this squad in the window: `damage_taken`, split into `from_opponents`, `from_ring`, `from_other_environment` and `from_own_squad`. Per attacking squad: damage, hits, last hit, and the mean bearing from victim to attacker at hit time |
| `pressure` | Opposing squads with fresh standing positions: distance, band, bearing, sector, `dz`; near and medium counts; sector counts; `too_close_for_bearing` |
| `ring` | `known`, `phase` (`closing`, `waiting`, `not_revealed`, `closing_target_unknown`), `label`, `ring_number`, `rings_closed`, `stage_raw` with `stage_raw_meaning`, `closing_remaining_s`; when known: `radius`, signed boundary distance per fresh member (positive is outside), `outside_players`, `nearest_inside_margin` |

**Ring numbering (contract version 2).** The replay's ring `stage` is a 0-based entry index (0–5 in game 123633). The two ring readers name a *waiting* ring differently, though they draw the same circle:

- the map's `map_config.current_ring` reports the next entry to close;
- this panel's `ring_features.ring_at` reports the last entry that closed.

`ring_label.py` turns both into one label: **ring N is the N-th closure**, so the raw index is N − 1. While closing, the label reads "Ring 4 closing". While waiting, it reads "Ring 3 closed · ring 4 next". The map status line, this panel and both AI packets show only that label. The raw index stays in `stage_raw`, with its meaning, and neither reader was changed. Model features do not use either reader.

**Which players count.** Only standing players (`alive` or `downed`) with a position updated within 5 s are used. Eliminated and dead players' last positions are listed under `quality` and never used. Environmental damage (attacker "World", teamId 0, no position) is not an attacking squad. It is split into ring damage (weapon "The Ring", 圓圈 or 安全区) and other environment (burn, wildlife).

## Interpretation limits

- A higher z is a height difference only. It does not establish usable high-ground control (terrain, cover, reachable angles).
- Distance and bearing do not establish line of sight.
- Incoming fire is **observed recent fire** from feed events, not current visibility. The attacker's direction is where they were when they hit.
- Squad centroids hide spread. Squads closer than 500 u get no bearing.

## Three inspected moments (game 123633, duo kval lobby 1, held-out)

Fire totals were cross-checked against a direct sum of the raw feed's damage events in the same window.

**1. 14:19 (t 928), Meaningless (11): crossfire.**
- 439 damage in 10 s, all from opponents: Gaymers 245 (11 hits, from the W) and пыхтение гепарда 194 (7 hits, from the NE).
- One squad within 3,000 u (Gaymers, 644 u), five more within 8,000 u, spread over 4 sectors.
- One member is downed.
- Ring 4 is closing (raw stage 3; 38 s left); both members are about 1,120 u inside.
- Height vs 5 nearby squads: higher than 1, lower than 3.

**2. 09:44 (t 653), team 15: caught outside a closing ring.**
- Both members are outside ring 2 as it closes (raw stage 1; +1,177 u and +137 u), with 26 s of closing left.
- 72 damage in 10 s: 40 from Meaningless (south, last hit 9 s ago) and 32 from the ring.
- One squad within 3,000 u (Meaningless, 1,426 u to the S).
- 13 opposing standing players had stale positions and were excluded.

**3. 18:48 (t 1197), EQT (20): the final knock.**
- 132 damage in 10 s, all from феечки винкс; hits came from the SE at hit time.
- The two squads' centroids are 113 u apart, too close for a bearing.
- EQT's mean height is 65 u below that squad's.
- The final ring stage is closing with no recorded end, so ring distance is not measured.

This is the same moment as the 30.5% held-out probability in `docs/dashboard.md`; that probability does not use any of these measurements.
