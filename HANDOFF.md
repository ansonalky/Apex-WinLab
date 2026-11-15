# Handoff

## Performance page (local import still required)

Team and player results are extracted from cached standings, not from replay frames. `teamId` is match-local. Rosters are Overstat `playerId` sets. Average points are used only when a game is compatible with this scoring scheme: every team's recorded score matches placement points plus one per kill. That match does not establish the official rules. Otherwise the page ranks by average placement.

This machine has no `DATABASE_URL`. Schema and queries are written. They have not been run against Tiger Data.

```sh
.venv/bin/python -m unittest tests.test_results -q
.venv/bin/python import_results.py --data-dir data --dry-run
set -a; . ./.env; set +a
.venv/bin/python import_results.py --data-dir data --check-reimport
.venv/bin/python app.py --data-dir data --port 5050 --backend auto
```

Open `/stats` after the import. Matches must already be in Tiger Data (`import_tiger.py --data-dir data --all`). The results import deletes and reinserts only `team_results` and `player_results`.

## Earlier collection notes

Milestone 3B discovery has started and stopped at the accepted-game target. Raw telemetry is gitignored.

## Rule

Accept a game only when its own `games[].analytics.qualityScore` is >= 7.0, replay validation passes, and the win label is verified. The score comes from `GET /api/stats/{sessionId}/overall`, matched by game `id`, not list order. Session-level quality is never copied onto a game. Zero is a real score. Missing stays unknown.

A win label is outcome metadata, not a player feature. It is kept only when one standings squad has `teamPlacement == 1` and that team id and name match the replay squad (`replay.teamsAlive` and/or `replay.player.teamPlacement`). Otherwise the reason stays `outcome_unknown`.

## First three qualifying games

These were the first three that cleared quality, replay, and the winner check:

| Game | Session | Event | Quality | Winner |
| --- | --- | --- | --- | --- |
| 123662 | 23234 | S5: Contenders B vs C Week 6 | 10.0 | HINU A |
| 123659 | 23233 | S5: Contenders A vs D Week 6 | 8.996 | Nebraska Esports |
| 123658 | 23237 | S5: Open G vs H Week 6 | 8.471 | Utah Delta |

Organizer for all three: octanecollegiate. The below-threshold games in session 23231 were not downloaded again.

## Catalog when the run stopped

Stopped because **accepted_target** was reached. Not because the session cap or request budget was exhausted.

- Sessions examined: 57 of the 100 cap. Search `next_start` is saved for resume.
- Individual games recorded: 282.
- Quality >= 7.0: 80. Of those, 54 had a replay id.
- Accepted: **51**. New downloads in the last process: 39. Requests: 113 of 400. Temporary failures: 0.
- Exclusions: below_quality_threshold 131, replay_missing 94, duplicate 3, outcome_unknown 2, reconstruction_failed 1.
- Accepted games span 23 sessions, 5 maps, and 11 organizers (APEX RU CUP, BattlegroundsHQ, ECAC Esports, EsportsOhio, Leadjoy Eternal Cup, hisandhers, kayoko, luckyshots, octanecollegiate, qunqing, veryapexpro).

Manifest: `data/collection/manifest.json`.

## Commands

```sh
.venv/bin/python -m unittest tests.test_collection tests.test_overstat_patch -q
.venv/bin/python collect_matches.py run --max-sessions 100 --accepted-target 50 --request-budget 400 --delay 0.25 --min-quality 7.0
.venv/bin/python collect_matches.py resume --max-sessions 100 --accepted-target 50 --request-budget 400 --delay 0.25 --min-quality 7.0
```

Empty `--name` searches public sessions. `--organizer` is the optional search filter. `--session 23231` still limits the walk to one session.

## Unresolved

Two qualifying replays stayed `outcome_unknown` because the standings squad and the replay squad did not agree. One download failed reconstruction. Coordinate units, `angles`, and the server-side quality formula are still undefined. Placement is not a model input.
