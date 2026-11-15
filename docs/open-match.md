# Open an Overstat match

The match page has an **Open Overstat match** field next to the match selector. Paste a public Overstat URL. The dashboard parses it and never fetches that URL. Replay and session requests go only to `https://overstat.gg/api/...`. A redirect off that API host is refused. HTTP 429 stops the attempt, keeps the session list when it was already downloaded, and asks for a manual retry. There is no automatic second request.

## URLs

These shapes come from Overstat's route table (`app.88b57234.js`, `2983.9143198a.js`, `9107.be2636c4.js`, `1265.c5830509.js`):

| Page | Meaning |
| --- | --- |
| `https://overstat.gg/tournament/{organizer}/{matchId}/replay/{game}` | One game. `{matchId}` may be `{id}.{event slug}`. `{game}` is the API field `game` (the game number), not the game id. |
| `https://overstat.gg/tournament/{organizer}/{matchId}` and `.../standings/{game}` | The session, or one game when `{game}` is a number. |
| `https://overstat.gg/replay/{matchId}/{game}` | Same replay view. `{game}` is again the game number. |

`overstat.gg` and `www.overstat.gg` are accepted, over `http` or `https`. Other hosts, and paths such as `/matches` or `/organizers`, are rejected. A session with several games asks which game to open and does not download telemetry until one is chosen.

## Save or preview

The download, decoder, integrity checks, and winner check are the collector's existing functions. Required telemetry or metadata that is missing is explained. Optional fields stay Unknown.

A replay is saved only when its stored quality is at least 7.0 (the original number, not the rounded label) and every acceptance check passes, including a verified winner. It is written to `data/user-imports/` (`package` `user-import`): its own manifest, `raw/`, and stats cache. That directory is registered for maps, rings, and coaching. The original manifest and the expansion manifest are not modified. The database write replaces frames, squads, and results for that game id only.

Below 7.0, unknown quality, or a failed acceptance check (including a playable replay with no verified winner) becomes a temporary preview when the replay can be shown. Previews live in `data/previews/<game_id>/` and are not match, frame, result, or prediction rows. Each `meta.json` has `expires_at` 24 hours after it was written. Startup and each open delete expired previews.

Opening a game id, livedata id, or `mid` that is already stored opens that match and does not download again. After a download, the raw sha256 is checked the same way. A second or overlapping submission of the same session and game does not create a second record.

## Predictions

Saved imports and previews use the same forward pass as the expansion games: unchanged `models/model.json` and its saved scaler, per second, with the winner left out of the inputs. The verified-unseen label is “New-match prediction · model trained on the original 51 games.” Membership compares the game id and the replay sha256 with `trained_on_games` and the original manifest hashes. If that cannot be verified, the page says so and coaching does not treat the estimate as independent evidence. A preview also says that performance on low-quality preview matches has not been established.

The per-second table is computed in the background. Seeking is not blocked. Saved matches persist `new_match` rows for that game only. Preview estimates stay in memory and are not written to Tiger Data. Original games keep their held-out and fitted rows.
