# Handoff

Milestone 3 trial is done for one known session. No features, model, database, or UI. Stop here. Do not expand to 50 games yet.

## Objective

Accept a unique game only when its per-game quality score is >= 7.0 and replay validation passes. This trial used session **23231** (Octane Collegiate, `https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3`).

## Score

Field: `games[].analytics.qualityScore` on `GET https://overstat.gg/api/stats/23231/overall`. Scope: **individual game**. The standings UI (chunks `243.1b8ae876.js` and `8575.c2d176bd.js`) shows `Math.floor(100 * stats.analytics.qualityScore) / 100` for the selected game object. Session `analytics.qualityScore` is separate (here 2.323) and is not copied onto games. The server owns the formula. Unknown stays unknown, not zero. A stored 0 is a real score and fails 7.0.

## Trial result

6 games, 5 with livedata. **Accepted: 0.** New downloads: 3. Temporary failures: 0.

| Game | Id | Quality | Livedata | Download | Validation | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 123536 | 2.979 | yes | new | outcome_unknown | below_quality_threshold |
| 2 | 123540 | 4.961 | yes | new | outcome_unknown | below_quality_threshold |
| 3 | 123544 | 1.275 | yes | cached sample, not rewritten | passed (team 20, Campbellsville) | below_quality_threshold |
| 4 | 123547 | 1.887 | no | not attempted | not run | replay_missing |
| 5 | 123550 | 2.835 | yes | new | outcome_unknown | below_quality_threshold |
| 6 | 123553 | 0 | yes | not attempted (cap) | not run | below_quality_threshold |

New files, bytes unchanged, 12:28 PM PT: `cbe8db3d-0a4a-4479-828a-4c92965ad8b7` sha256 `eb60f9415bd9…`, `7c8ac260-c90c-48a2-aa04-200fed795756` sha256 `632f46fd048d…`, `220adc2c-fcb3-46be-9f96-c50d5cee8aaf` sha256 `1408069079fd…`. Manifest: `data/collection/manifest.json`. The milestone 1 raw file was not modified.

The three new replays reconstructed. Playing checkpoints had positions and team ids (58 or 60 players). Raw series still includes the frontend-omitted last `t`. Winner rule failed: `teamsAlive == 1` but no single non-eliminated, non-dead team. `teamPlacement` was not used. The cached sample did identify one such squad, then quality excluded it.

## Decoder interface

`overstat_patch.replay(series, checkpoints=None)` starts at `{feed: []}`, applies `patchData` (`d` then append `f`), and returns `state`, `observed`, `state_names`, `failures`, `snapshots`, `checkpoint_meta`, `frame_count`, `omitted_t`. `omitted_t` is `series[-1].t`, which the frontend drops via `new Array(last.t)`. `snapshots[t]` is `export_players`: each row has `nucleusHash`, and `name`, `teamId`, `teamName`, `health.current`, `health.max`, `shields.current`, `shields.max`, and `status` as `{value, observed_at, gap}`. `pos` is `{value:{x,y,z}, observed_at, gap}`. Null value and null gap mean unknown. `gap` 0 was written on that frame.

`apply_frame(state, delta, events, observed, t)` returns `(state, observed, error)`. `unsupported_deltas` flags text diffs and other non-patch nodes; those fail validation instead of being skipped.

Explicit checks, not freshness thresholds: quality >= 7.0; a livedata payload; no unsupported ops or patch failures; Playing exists; each of the three Playing checkpoints has one player with `pos.x` and `teamId`; winner is `teamsAlive == 1` and exactly one team not `eliminated` or `dead`.

## Commands

```sh
.venv/bin/python -m unittest tests.test_collection tests.test_overstat_patch -q
.venv/bin/python collect_matches.py preview --session 23231 --min-quality 7.0
.venv/bin/python collect_matches.py trial --session 23231 --limit-games 10 --max-downloads 3 --min-quality 7.0
.venv/bin/python collect_matches.py resume --session 23231 --max-downloads 3 --min-quality 7.0
```

`run --accepted-target 50` only walks this one session. It was not executed. Fifty accepted games needs more sessions later.

## Files and tests

`collect_matches.py`, `collection.py`, `tests/test_collection.py`, `overstat_patch.py` (`unsupported_deltas`), `download_match.py` reused for hash, atomic write, and the single-sample manifest. Tests: 27 passed.

## Unresolved

No game in session 23231 met 7.0, so nothing was accepted. Winner evidence is missing when every player is `eliminated` or `dead`, or when a second team is still `downed`. Coordinate units and `angles` are still undefined. The quality formula is not in the frontend.
