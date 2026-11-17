# Position-feature v2: ring-relative features

An experiment. The frozen v1 model, its split, its reports, and the v1/v2 held-out bundles are unchanged and do not use these features. Code: `ring_features.py`, `ring_experiment.py`. Tests: `tests/test_ring_features.py`.

## Result (2026-11-14)

**Ring-relative position features do not reliably improve the model.** All comparisons use train and validation sessions only, the frozen `l2 = 0.01` (no tuning), and eligibility-constrained probabilities. The 6 frozen test games were excluded, and the script asserts they never enter.

| Game-weighted log loss | Train → validation (9 games) | Leave-one-session-out (45 games, 18 sessions) |
| --- | --- | --- |
| Condition only | 2.532 | 2.461 |
| Condition + ring position | 2.543 | 2.456 |
| Condition + v1 position (reference) | 2.516 | 2.459 |
| Contention uniform | 2.549 | 2.491 |

Ring minus condition, per game, with a 2,000-sample game bootstrap:
- Train → validation: +0.011 nats, 95% CI −0.041 to +0.053; better in 4 games, worse in 5.
- Leave-one-session-out: −0.005 nats, CI −0.020 to +0.012; better in 30, worse in 15.

Both intervals include zero. The v1 position features are equally inconclusive under leave-one-session-out (−0.002, CI −0.010 to +0.006), although they beat condition-only in all 9 validation games. Every model is only about 0.03 nats better than contention uniform under leave-one-session-out.

## Ring semantics (verified)

Checked on all 51 accepted games. The independent ground truth is 22,035 ring-damage events (`weapon` "The Ring" or its localized names), which record the victim's position.

- **Sizes are radii.** If `startSize`/`endSize` were radii, 95.7% of ring-damage victims would be outside the ring. Of the rest, 876 are within 5% of the boundary (damage-tick timing) and 62 (0.3%) are clearly inside. Only 1.5% of 219,071 sampled undamaged standing players would be outside. Read as diameters, 56% of undamaged players would be "outside", which is not plausible.
- **Closing is linear.** While a stage closes (`start ≤ t < end`), center and radius move linearly from start to end. Between closings, the ring is the last stage's end circle.
- **The `t = 0` state contains the whole planned schedule**, including future centers. It is never used. The plan can also be revised mid-match: in game 123552, stages 4 and 5 differed from the plan.
- **Live reveal.** A stage's entry is inserted into `rings[]` when that stage starts closing (305 inserts). A stage counts as revealed only from its insertion, identified by the decoder's observation stamp being greater than 0.
- **`ring.state` / `ring.stage` are unreliable before the first closing.** They disagree with the timing in 5,501 frames. They are not used.

**Explicit unknowns** (value `None` plus a `*_missing` flag):
- No live entry yet (`not_revealed`): 224 of 1,807 checkpoint groups.
- A closing whose live entry has no `end`/`endCenter` (`closing_target_unknown`, usually the final ring): 89 groups.
- Squads with no standing player, or no standing position.

Time until the next closing exists only in the revisable plan, so it is not used. Ring geometry is known for 83% of squad rows; a squad position relative to the ring for 61%.

## Features

All computed per squad from standing players (`alive` or `downed`) at the checkpoint. `r` is the current ring radius.

| Feature | Definition |
| --- | --- |
| `ring_center_dist_rel` | Distance from squad centroid to ring center / r |
| `ring_boundary_signed_rel` | Mean over members of (distance to center − r) / r; positive is outside |
| `ring_outside_frac` | Fraction of members outside the ring |
| `ring_spread_rel` | Mean member distance to squad centroid / r |
| `ring_nearest_enemy_rel` | Nearest other standing squad centroid / r |
| `ring_nearby_enemies` | Other squads within **0.2 r** (ring-relative, because coordinate units are unverified) |
| `ring_outside_frac_closing`, `ring_boundary_signed_closing` | The two above while the ring is closing, else 0 |
| `ring_geometry_missing`, `ring_squad_position_missing` | Unknown flags |

Ring phase and remaining closing time are the same for every squad in a group, so on their own they cancel out of a conditional logit. They enter only through the closing interactions.

Proximity is distance only. It says nothing about cover, terrain, or line of sight.

## Reproduce

```sh
.venv/bin/python -m unittest tests.test_ring_features -q
.venv/bin/python ring_experiment.py     # -> data/experiments/ring-v2/report.json
```
