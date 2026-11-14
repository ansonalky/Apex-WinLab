# Handoff

Paused after Milestone 2. Working tree was clean before this file. No payload or bundle was reread for this note.

## Objective and milestone

Apex WinLab turns public Overstat telemetry into model-ready historical features. Do not build features, a model, a database, or a UI yet.

Milestone 1 (done): download one timeseries and inspect its structure. Milestone 2 (done): reproduce the replay frontend's historical decoder and export three active-play player snapshots. Stop here.

Sample id: `623ae07f-81cf-4634-ad7f-2d27f7911896`. Branch `main` at `e1e1dbb` before this handoff (`920f1e6` download/inspect, `e1e1dbb` decoder). Pushed to `origin/main`.

## Completed work and commands

Python 3.12 venv at `.venv` (stdlib only). Raw file and manifest are gitignored under `data/`.

```powershell
.\.venv\Scripts\python -m venv .venv
.\.venv\Scripts\Activate.ps1
python download_match.py
python inspect_match.py
python -m unittest tests.test_overstat_patch -v
python reconstruct_match.py
python reconstruct_match.py --t 62 --t 639 --t 1217
```

`reconstruct_match.py` writes `data/derived/checkpoints.json`. Default checkpoints are the start, midpoint, and end of reconstructed `Playing`: **t = 62, 639, 1217**.

## Decoder

`patchData(state, d, f)` is in the public bundle. It calls jsondiffpatch `patch` on `d`, then appends `f` to `state.feed`. `f` does not change player fields.

- Replay page: https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3
- Route table: https://overstat.gg/js/app.88b57234.js (`tournament.replay` loads chunk 9107)
- Caller: https://overstat.gg/js/9107.be2636c4.js
- `patchData` and bundled patcher: https://overstat.gg/js/8333.38781711.js (webpack modules 2003 and 3121)

One-element `[new]` sets the value. Two-element `[old, new]` sets `new` (not a merge). `[old, 0, 0]` deletes an object key. `_t: "a"` is an array edit. Text diffs throw; the default patcher has no diff-match-patch.

Initial state is `{feed: []}`. The timeline is `new Array(series[series.length - 1].t)`, so **t = 1251** (`Resolution` to `Postmatch`) is not applied. Reconstructed state ends at `Resolution`. `payload.match` stays separate (`Postmatch`, `teamsAlive` 1) under `final_outcomes_not_model_input`.

`t` is seconds after `match_start`. All 1,993 events match `int(timestamp) - int(match_start) == t`. The t = 62 delta also writes `game_start` and enters `Playing`; `game_start - match_start` is 62. The frontend clock uses `match_start`, not `game_start`.

Checkpoints: 60 players, 20 teams. Reconstructed `teamsAlive` is 20, then 11, then 2. Missing fields stay null. `gap` 0 means written on that frame; a larger gap is carried forward. Python matched the bundled patcher on all 1,980 compared player fields. 0 patch failures. Report: `docs/historical-state-decoder.md`.

## Collection policy

Collect only matches with **quality >= 7.0 AND validated replay data**. Both are required. This policy is not implemented in code yet. The one sample above was already downloaded before the policy was recorded here; do not treat that download as a collection pass.

## Files, tests, unresolved

Changed: `.gitignore`, `requirements.txt`, `download_match.py`, `inspect_match.py`, `overstat_patch.py`, `reconstruct_match.py`, `tests/__init__.py`, `tests/test_overstat_patch.py`, `docs/historical-state-decoder.md`, this file.

Tests: `python -m unittest tests.test_overstat_patch -v` — 22 tests, OK (about 0.17s). The sample hash test runs only when the raw file is present.

Unresolved: this file has no text diffs or array moves. Array deltas only edit `observers.*.targetTeam`; the meaning of `observers` is unknown. Coordinate units and `angles` are not defined by `patchData`. Why t = 0 player objects are replaced at t = 1–2 is unknown. Whether dropping t = 1251 is intentional is unknown. A throw after a nested write would keep that write; this sample never throws. Status strings seen: `alive`, `downed`, `eliminated`, `dead`.

## Processes and next action

No project process is running. Platform node and editor processes are not part of the replay.

Next action: do not reread the payload or the JS bundles. Do not start features, modeling, a database, or a UI. When collection resumes, admit a match only if quality is at least 7.0 and its replay data is validated, then run the existing decoder on those matches.
