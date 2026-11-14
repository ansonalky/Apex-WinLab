# Historical state decoder

Overstat's replay page applies each timeline entry with `patchData(currentState, entry.d, entry.f)`. That function is a jsondiffpatch `patch` of `d`, then an append of `f` onto `state.feed`. Reconstruction uses that patcher. It does not merge objects, and it does not treat a data array as a delta just because of its length.

## Sources

Replay page: https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3

| Piece | URL |
| --- | --- |
| App shell and route table | https://overstat.gg/js/app.88b57234.js |
| `playCurrentFrame` / `loadGameTimeline` | https://overstat.gg/js/9107.be2636c4.js |
| `patchData` (webpack module 2003) and the bundled jsondiffpatch patcher (webpack module 3121) | https://overstat.gg/js/8333.38781711.js |

The route `tournament.replay` loads chunks 9549, 4312, 9090, 8333, 9107, and CSS-only 7958. `patchData` is not in the app shell.

## Semantics taken from that code

`patchData(state, d, f)`:

1. If `d` is truthy, `state = jsondiffpatch.patch(state, d)`. On a throw, that assignment is skipped. Nested edits the collector already wrote stay written.
2. `state.feed = state.feed ?? []`.
3. If `f` is truthy, `state.feed.push(...f)`.
4. Return `state`.

The bundled patcher is constructed with no options, so it has no diff-match-patch library.

Delta arrays are the patcher's atomic edits. A JSON array that is the *value* inside a one-element or two-element delta is ordinary data.

- One element, `[newValue]`: the property becomes `newValue`.
- Two elements, `[oldValue, newValue]`: the property becomes `newValue`. The old element is not read.
- Three elements, `[oldValue, 0, 0]`: an object property is deleted. An array slot becomes JavaScript `undefined` and stays in the array.
- Three elements, `[diffText, 0, 2]`: a text diff. The default patcher throws because diff-match-patch was not provided.
- An object with `_t: "a"` is an array edit: an index key whose value is a one-element array inserts; any other value at an index is a nested edit of that slot; a `_<index>` key of `[value, 0, 0]` removes; `[value, newIndex, 3]` moves. Removals run from high index to low, then insertions and moves, then slot edits.

`d` versus `f`: `d` is the state delta. `f` is the list of events appended to the feed after the patch. Events do not change player fields. The same module's `mapTeams` / `combindTeams` helpers only group the already patched players for the scoreboard UI. They are not part of the decoder.

Initialization: the component starts at `currentState = {feed: []}`. It does not copy `payload.match` into that state. `loadGameTimeline` replaces `series` with `new Array(series[series.length - 1].t)` and, for each index `s`, uses the entry with `t == s` or `{t: s, f: []}`. The entry whose `t` equals that length is never applied.

`t`: `playCurrentFrame` sets `currentTime` to the frame's `t`. Event alignment is `parseInt(match.match_start) + currentTime`, and the seek helpers subtract `match_start` from `event.timestamp`. On this file, all 1,993 events satisfy `int(timestamp) - int(match_start) == t`. `game_start` is a field written by the delta at `t = 62`, together with the transition to `Playing`. Here `game_start - match_start` is also 62. The frontend clock uses `match_start`, not `game_start`.

## What this sample does with those rules

1,251 frames are applied (`t` 0 through 1250). `t` 1251 (`state` from `Resolution` to `Postmatch`) is the omitted tail. After the replay the reconstructed `state` is `Resolution`. `payload.match.state` remains `Postmatch` and is stored only under `final_outcomes_not_model_input`.

`Playing` is `t` 62 through 1217. The three checkpoints are the first of those frames, the integer midpoint, and the last: **62, 639, and 1217**. There were 0 patch failures. Each checkpoint has 60 players on 20 teams. Reconstructed `teamsAlive` is 20, then 11, then 2. The separate final object says `teamsAlive` 1.

A missing coordinate, health, shield, or status stays `null` with `observed_at` and `gap` null. A value written earlier and not written again keeps its old `observed_at`; `gap` is `checkpoint_t - observed_at`. `gap` 0 is a write on that frame. `gap` greater than 0 is carried forward, not a new observation. Position axes are tracked separately, because a `pos` delta often names only the axes that changed.

At `t` 62, Podge (`61234bac26d84f616124206113cb1a95`, team Southeastern) is at `(-2640, -21684, 6377)` with health 100 and shields 50, all written on that frame. Status is `alive`, last written at `t` 52, so the gap is 10. Name and team were last written at `t` 1.

Status strings seen on the checkpoints are `alive`, `downed`, `eliminated`, and `dead`. The patcher does not define them.

The Python replay matched the bundled `patch` on all 1,980 compared player fields (identity, team, position, health, shields, status) at the three checkpoints.

## Reproduce

From the repository root, after `download_match.py` has written the sample:

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_overstat_patch -v
python reconstruct_match.py
python reconstruct_match.py --t 62 --t 639 --t 1217
```

`reconstruct_match.py` writes `data/derived/checkpoints.json`. The default checkpoints are the Playing start, midpoint, and end. `--t` repeats once per checkpoint.

## Unresolved

- Text diffs and array moves are part of the bundled patcher and are implemented, but this file has neither. Its 272 array deltas only insert or edit `observers.*.targetTeam`. Its 487 three-element deltas delete object keys, mostly inventory counts. What `observers` means is unknown.
- Coordinate units, and the meaning of `angles`, are not in `patchData`.
- The producer's reason for the `t` 0 player blob, which `t` 1 and `t` 2 then replace with smaller player objects, is unknown. The replacement is applied as a one-element delta, not merged.
- Whether `new Array(last.t)` drops the final frame on purpose is unknown. This replay drops it because the frontend does.
- `game_start - match_start == 62` is true for this file and is the same `t` at which the delta enters `Playing`. The frontend source does not define `t` as `game_start - match_start`; it defines `t` against `match_start`.
- A delta that throws after a nested edit has already been written would leave that nested edit in place, as the bundle's collector does. This file never throws, so that path is unexercised on the sample.
