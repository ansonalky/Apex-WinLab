# Performance in collected matches

The page is the accepted games in this project. It is not a global ranking.

## Source fields

For each accepted game, `result_rows.py` reads `data/collection/cache/stats-{session}.json` and the game whose `id` equals the manifest `game_id`.

- Team placement, kills, damage, assists, and score come from `teams[].overall_stats` (`teamPlacement`, `kills`, `damageDealt`, `assists`, `score`).
- Player kills, damage, and assists come from `teams[].player_stats`.
- Missing values stay NULL. They are not stored as zero.
- Replay frames are not summed.

## Identity

`teamId` is match-local. Rosters are the sorted Overstat `playerId`s on that team in that game. The same numeric `teamId` in another game is not the same team. The same name with a different roster is a different row. A roster recorded under more than one name is flagged.

## Scoring and format

A game is `algs_placement_plus_kill` only when every team's score equals placement points (12, 9, 7, 5, 4, 3, 3, 2, 2, 2, 1 for 11th through 15th, then 0) plus one per kill. Average points use that recorded score and do not add kills again. Games that fail the check are ranked by average placement. Trios and duos stay separate unless the format filter is "all formats", which also ranks by placement.

Squad format is trios when the largest recorded squad in the game has 3 players, duos when it has 2, and solos when it has 1. A short squad in a trio game does not change the game's format; the row is flagged.

## Queries

`stats_query.py` aggregates with parameterized SQL on `team_results` and `player_results` joined to `matches`. Those statements do not reference `player_frames`. The player query joins `team_results` on `(game_id, roster_key)`, which is one team row per roster in a game.
