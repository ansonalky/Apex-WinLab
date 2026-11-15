# Expansion transfer (49 accepted games)

The frozen production manifest and evaluation files were not modified. These 49 games are a separate collection. The transfer archive does not resume collection and does not import rows into Tiger Data.

Archive on this machine: `/opt/cursor/artifacts/apex-expansion-49.tar.gz` (51,193,078 bytes).

## Extract

Do not extract this onto the production `data/` directory.

```bash
mkdir -p "$HOME/Apex-WinLab-expansion"
tar -xzf apex-expansion-49.tar.gz -C "$HOME/Apex-WinLab-expansion"
cd "$HOME/Apex-WinLab-expansion/apex-expansion-49"
python3 verify_package.py
```

`verify_package.py` recomputes every raw-file hash. It does not contact Overstat.

## Contents

- `data/collection/manifest.json` — the 49 accepted games only. Transfer snapshot, not a resume cursor.
- `data/collection/cache/games-<session>.json` — 32 session game lists.
- `data/collection/cache/stats-<session>.json` — 32 standings caches. Match a game by `id`, not by array position.
- `data/raw/<livedata>.timeseries.json` — 49 original telemetry files.
- `data/manifests/<livedata>.timeseries.manifest.json` — `source_url`, `retrieved_at`, relative `filename`, and `sha256`.
- `INVENTORY.json` — one row per game (id, session, date, map, sha256, file names) and the checks below.

## Checks

All 49 raw files match the sha256 in the collection record and in the retrieval manifest. Inside the package there are no repeated game ids or replay hashes. Against the original 51 accepted games there is no shared game id, livedata id, mid, or replay hash. None of the 49 game ids appear anywhere in the frozen manifest (282 records). Frozen manifest sha256: `347c447fe1952f8783724ae6ad2af7d0c165d1ed84409c08b1c4d4f4517ccd4f`.

## Rate limiting

A repeated HTTP 429 now saves the manifest and stops the run. If the response includes `Retry-After` (delay seconds or an HTTP date), the client waits that long and tries the same request once. It does not continue into further sessions. Queued session ids from the earlier 429s are stored as `rate_limited_session_ids` and are not requested automatically.
