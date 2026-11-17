"""Open one public Overstat URL in the dashboard.

Supported pages come from Overstat's route table (``app.88b57234.js``):

- ``/tournament/{organizer}/{matchSlug}/replay/{game}`` (``tournament.replay``).
  ``matchSlug`` is ``{matchId}`` or ``{matchId}.{eventSlug}``. The replay page
  splits the slug on ``.`` and uses the first piece as ``matchId``
  (``2983.9143198a.js``). ``game`` is the API field ``game``, matched with
  ``==`` in ``9107.be2636c4.js``, not the numeric game id.
- ``/tournament/{organizer}/{matchSlug}`` and ``.../standings/{game}``.
  ``game`` of ``overall`` or omitted is the session. A numeric ``game`` selects
  one game.
- ``/replay/{matchId}/{gameId}`` (``replay-viewer``). The viewer passes
  ``gameId`` to the same replay component as its ``game`` prop, so it is the
  game number as well.

The pasted URL is parsed and never fetched. Requests go only to
``https://overstat.gg/api/...`` and a redirect off that host is refused.
HTTP 429 is returned as a retryable state with no automatic second request.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from collection import decide, validate_timeseries
from collect_matches import Unavailable, discover_session, fetch_telemetry
from dashboard_data import collection_name, register_expansion_dir
from download_match import sha256_bytes
from match_inference import SAVE_QUALITY, save_quality

API = "https://overstat.gg/api"
ALLOWED_HOSTS = {"overstat.gg", "www.overstat.gg"}
USER_IMPORT = "user-import"
_LOCK = threading.Lock()
_BUSY: set[tuple] = set()
_GUARDS: dict[int, threading.Lock] = {}


def _session_guard(session_id: int) -> threading.Lock:
    """One in-flight open per session, so concurrent submits share one cache write."""
    with _LOCK:
        return _GUARDS.setdefault(int(session_id), threading.Lock())


class RateLimited(Exception):
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__("Overstat returned HTTP 429")


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_api(newurl):
            raise Unavailable(f"refusing redirect off the Overstat API: {urllib.parse.urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _allowed_api(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "overstat.gg"
        and (parsed.path or "").startswith("/api/")
    )


class ApiClient:
    """One attempt per URL. 429 is not retried."""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(SameHostRedirect)

    def get(self, url: str) -> bytes:
        if not _allowed_api(url):
            raise Unavailable("refusing a request outside the Overstat API")
        request = urllib.request.Request(url, headers={"User-Agent": "ApexWinLab/0.4 (open match)"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except Unavailable:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = None
                if exc.headers is not None:
                    raw = exc.headers.get("Retry-After")
                    try:
                        retry_after = float(raw) if raw else None
                    except (TypeError, ValueError):
                        retry_after = None
                raise RateLimited(retry_after) from None
            if 400 <= exc.code < 500:
                raise Unavailable(f"HTTP {exc.code}") from None
            raise Unavailable(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Unavailable("the Overstat request failed") from exc


def parse_overstat_url(text: str) -> dict:
    """Session id and optional game number. Raises ValueError for anything else."""
    raw = (text or "").strip()
    if not raw or any(ord(ch) < 32 for ch in raw):
        raise ValueError("Paste a public Overstat match, session, or replay URL.")
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in ALLOWED_HOSTS:
        raise ValueError("Only public overstat.gg match, session, and replay URLs can be opened.")
    parts = [part for part in parsed.path.split("/") if part]
    session_id, game_number, kind = _route(parts)
    if session_id is None:
        raise ValueError(
            "That Overstat page is not a match, session, or replay URL. "
            "Use a /tournament/…/replay/… link, a tournament standings link, or /replay/{matchId}/{game}."
        )
    return {"session_id": session_id, "game_number": game_number, "kind": kind}


def _digits(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return None


def _session_id(slug: str) -> int | None:
    """The tournament shell uses ``matchSlug.split('.')[0]`` as the match id."""
    head = slug.split(".", 1)[0]
    return _digits(head)


def _route(parts: list[str]) -> tuple[int | None, int | None, str]:
    if len(parts) == 3 and parts[0] == "replay":
        session_id = _digits(parts[1])
        game_number = _digits(parts[2])
        if session_id is None or game_number is None:
            return None, None, ""
        return session_id, game_number, "replay"
    if len(parts) >= 2 and parts[0] == "tournament":
        session_id = _session_id(parts[2]) if len(parts) >= 3 else None
        if session_id is None:
            return None, None, ""
        rest = parts[3:]
        if not rest:
            return session_id, None, "session"
        if rest[0] == "replay":
            if len(rest) == 1:
                return session_id, None, "replay"
            if len(rest) == 2 and (game_number := _digits(rest[1])) is not None:
                return session_id, game_number, "replay"
            return None, None, ""
        if rest[0] == "standings":
            if len(rest) == 1 or (len(rest) >= 2 and rest[1] == "overall"):
                return session_id, None, "session"
            if len(rest) >= 2 and (game_number := _digits(rest[1])) is not None:
                return session_id, game_number, "session"
            return session_id, None, "session"
        if rest[0] in ("stats", "drops"):
            return session_id, None, "session"
        return None, None, ""
    return None, None, ""


def _manifest_games(directory: Path) -> list[dict]:
    path = directory / "collection" / "manifest.json"
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    games = document.get("games") if isinstance(document, dict) else None
    if not isinstance(games, dict):
        return []
    return [row for row in games.values() if isinstance(row, dict)]


def _directories(data_dirs: list[Path], import_dir: Path) -> list[Path]:
    found: list[Path] = []
    for directory in list(data_dirs) + [import_dir]:
        path = Path(directory)
        if all(path.resolve() != earlier.resolve() for earlier in found):
            found.append(path)
    return found


def catalog(directories: list[Path]) -> dict[str, dict]:
    """game id, livedata id, mid, and sha256 already stored anywhere we read."""
    found: dict[str, dict] = {"game_id": {}, "livedata_id": {}, "mid": {}, "sha256": {}}
    from preview_store import configured

    roots = list(directories)
    preview_root = configured()
    if preview_root is not None and preview_root.is_dir():
        for child in preview_root.iterdir():
            meta = child / "meta.json"
            if meta.is_file():
                try:
                    roots.append(child)
                except OSError:
                    pass
    for directory in roots:
        rows = _manifest_games(directory)
        meta_path = directory / "meta.json"
        if meta_path.is_file() and not rows:
            try:
                rows = [json.loads(meta_path.read_text(encoding="utf-8"))]
            except (OSError, json.JSONDecodeError):
                rows = []
        for row in rows:
            where = {"game_id": row.get("game_id"), "directory": str(directory)}
            for key in ("game_id", "livedata_id", "mid", "sha256"):
                value = row.get(key)
                if value not in (None, ""):
                    found[key].setdefault(str(value), where)
    return found


def _hit(index: dict, key: str, value: Any) -> dict | None:
    if value in (None, ""):
        return None
    return index[key].get(str(value))


def _choice(record: dict) -> dict:
    return {
        "game_number": record.get("game_number"),
        "game_id": record.get("game_id"),
        "map_name": record.get("map_name"),
        "event": record.get("event"),
        "quality_score": record.get("quality_score"),
        "has_replay": bool(record.get("livedata_id")),
    }


def _playable(validation: dict) -> bool:
    if validation.get("reason") in ("invalid_payload", "reconstruction_failed", "insufficient_position_data"):
        return False
    return bool(validation.get("playing_t"))


def _write_manifest(directory: Path, game_id: int, record: dict) -> None:
    path = directory / "collection" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        document = {"version": 1, "package": USER_IMPORT, "games": {}}
    if not isinstance(document, dict):
        document = {"version": 1, "package": USER_IMPORT, "games": {}}
    document["package"] = USER_IMPORT
    games = document.setdefault("games", {})
    games[str(game_id)] = record
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _save_local(import_dir: Path, record: dict, raw: bytes, stats_document: dict | None) -> None:
    game_id = int(record["game_id"])
    livedata = record["livedata_id"]
    raw_path = import_dir / "raw" / f"{livedata}.timeseries.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists():
        raw_path.write_bytes(raw)
    record = dict(record)
    record["download_path"] = raw_path.as_posix()
    record["acceptance_status"] = "accepted"
    record["reason"] = "accepted"
    _write_manifest(import_dir, game_id, record)
    if stats_document is not None and record.get("session_id") is not None:
        cache = import_dir / "collection" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"stats-{record['session_id']}.json").write_text(
            json.dumps(stats_document) + "\n", encoding="utf-8"
        )
    if collection_name(import_dir) == USER_IMPORT:
        register_expansion_dir(import_dir)


def commit_saved_match(import_dir: Path, game_id: int, connection) -> dict:
    """Insert one saved import. Other games' frames, results, and predictions stay."""
    from import_results import replace_results
    from import_tiger import Source, import_game
    from result_rows import load_results

    source = Source(import_dir, int(game_id))
    # No evaluation report, and a user-import collection produces no frozen estimates.
    imported = import_game(connection, source, None)
    result_note = None
    try:
        teams, players = load_results(import_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        teams, players = [], []
        result_note = str(exc)
    teams = [row for row in teams if int(row["game_id"]) == int(game_id)]
    players = [row for row in players if int(row["game_id"]) == int(game_id)]
    if teams or players:
        replace_results(connection, teams, players, [int(game_id)])
        result_note = "results replaced for this game only"
    return {"import": imported, "results": result_note}


def _existing(existing: dict, import_dir: Path, database, *, before_download: bool) -> dict:
    """Open a replay we already have. A user import retries its database write."""
    game_id = int(existing["game_id"])
    directory = Path(existing.get("directory") or "")
    preview = (directory / "meta.json").is_file() and not (directory / "collection" / "manifest.json").is_file()
    message = (
        "This match is already openable. No second copy was saved."
        if before_download else
        "This replay is already stored. No second copy was saved."
    )
    if not preview and database == "auto" and directory.is_dir() and directory.resolve() == Path(import_dir).resolve():
        note = _save_database(import_dir, game_id)
        if note:
            message = f"{message} {note}"
    return {
        "status": "exists",
        "storage": "preview" if preview else "saved",
        "message": message,
        "game_id": game_id,
    }


def open_match(
    text: str,
    *,
    data_dirs: list[Path],
    import_dir: Path,
    cache_dir: Path,
    client,
    game_number: int | None = None,
    database: Any = None,
) -> dict:
    """Resolve a URL to an existing match, a saved import, a preview, or an error.

    ``database`` is a ``(import_dir, game_id)`` saver. ``None`` skips the
    database, which previews always do. The dashboard passes ``"auto"``, which
    uses ``DATABASE_URL`` when it is set and never prints it.
    """
    try:
        parsed = parse_overstat_url(text)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if game_number is not None:
        parsed = dict(parsed) | {"game_number": int(game_number)}
    with _session_guard(parsed["session_id"]):
        return _open_parsed(
            parsed,
            data_dirs=data_dirs,
            import_dir=import_dir,
            cache_dir=cache_dir,
            client=client,
            database=database,
        )


def _open_parsed(parsed, *, data_dirs, import_dir, cache_dir, client, database) -> dict:
    from preview_store import cleanup

    cleanup()
    try:
        records, _meta = discover_session(parsed["session_id"], cache_dir, client)
    except RateLimited as exc:
        return {
            "status": "retry",
            "message": "Overstat rate-limited this request. Nothing was saved. Wait and try the same URL again.",
            "retry_after": exc.retry_after,
        }
    except Unavailable:
        return {"status": "unavailable", "message": "Overstat did not return this session. No replay was saved."}
    except SystemExit:
        return {"status": "unavailable", "message": "Overstat did not return a game list for this session. No replay was saved."}
    playable = [row for row in records if row.get("livedata_id") and row.get("game_id") is not None]
    chosen = None
    if parsed["game_number"] is not None:
        chosen = next((row for row in playable if row.get("game_number") == parsed["game_number"]), None)
        if chosen is None:
            return {
                "status": "unavailable",
                "message": f"Game {parsed['game_number']} in session {parsed['session_id']} has no replay telemetry.",
                "games": [_choice(row) for row in records],
            }
    elif len(playable) == 1:
        chosen = playable[0]
    elif len(playable) == 0:
        return {
            "status": "unavailable",
            "message": "This session has no replay telemetry, so it cannot be opened.",
            "games": [_choice(row) for row in records],
        }
    else:
        return {
            "status": "choose",
            "message": "This session has more than one game. Choose one to open.",
            "session_id": parsed["session_id"],
            "games": [_choice(row) for row in playable],
        }
    index = catalog(_directories(data_dirs, import_dir))
    existing = _hit(index, "game_id", chosen.get("game_id")) or _hit(index, "livedata_id", chosen.get("livedata_id"))
    existing = existing or _hit(index, "mid", chosen.get("mid"))
    if existing and existing.get("game_id") is not None:
        return _existing(existing, import_dir, database, before_download=True)
    busy_key = (int(parsed["session_id"]), int(chosen["game_id"]))
    with _LOCK:
        if busy_key in _BUSY:
            return {"status": "retry", "message": "This match is already being opened. Wait for that attempt to finish."}
        _BUSY.add(busy_key)
    try:
        return _finish(chosen, data_dirs, import_dir, cache_dir, client, database, busy_key)
    finally:
        with _LOCK:
            _BUSY.discard(busy_key)


def _finish(chosen, data_dirs, import_dir, cache_dir, client, database, busy_key) -> dict:
    from preview_store import write_preview

    staging = cache_dir / "staging"
    try:
        fetched = fetch_telemetry(chosen, staging, client)
    except RateLimited as exc:
        return {
            "status": "retry",
            "message": "Overstat rate-limited the replay download. The session list was kept. Wait and try again.",
            "retry_after": exc.retry_after,
            "game_id": chosen.get("game_id"),
        }
    if fetched.get("download_status") not in ("downloaded", "cached"):
        return {
            "status": "unavailable",
            "message": fetched.get("error") or "The replay telemetry could not be downloaded.",
            "game_id": chosen.get("game_id"),
        }
    raw_path = Path(fetched["download_path"])
    raw = raw_path.read_bytes()
    digest = sha256_bytes(raw)
    index = catalog(_directories(data_dirs, import_dir))
    existing = _hit(index, "sha256", digest) or _hit(index, "game_id", chosen.get("game_id"))
    if existing and existing.get("game_id") is not None:
        return _existing(existing, import_dir, database, before_download=False)
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "unavailable", "message": "The replay file is not valid telemetry."}
    stats_game = _stats_game(cache_dir, chosen)
    validation = validate_timeseries(payload, stats_game)
    record = dict(chosen)
    record["sha256"] = digest
    record["download_status"] = fetched.get("download_status")
    if fetched.get("invalid"):
        return {"status": "unavailable", "message": "The replay file is not valid telemetry.", "game_id": record.get("game_id")}
    record["validation"] = validation
    record["validation_status"] = "passed" if validation.get("ok") else "failed"
    status, reason = decide(record, validation, float(SAVE_QUALITY))
    record["acceptance_status"] = status
    record["reason"] = reason
    if status == "accepted" and reason == "accepted" and save_quality(record.get("quality_score")):
        return _save(record, raw, import_dir, cache_dir, database)
    if not _playable(validation):
        detail = validation.get("detail") or validation.get("reason") or "the replay could not be reconstructed"
        return {
            "status": "unavailable",
            "message": f"This replay cannot be shown: {detail}.",
            "game_id": record.get("game_id"),
        }
    write_preview(int(record["game_id"]), record, raw)
    why = _preview_reason(record)
    return {
        "status": "ready",
        "storage": "preview",
        "message": f"Temporary preview. {why} It expires after 24 hours and is not written to the database.",
        "game_id": int(record["game_id"]),
        "quality_score": record.get("quality_score"),
    }


def _preview_reason(record: dict) -> str:
    reason = record.get("reason")
    if reason == "below_quality_threshold":
        return "Quality is below 7.0, so this match was not saved."
    if reason == "quality_unknown":
        return "Quality is unknown, so this match was not saved."
    if reason == "outcome_unknown":
        return "The replay plays, but the winner is not verified, so this match was not saved."
    return "Acceptance checks did not pass, so this match was not saved."


def _stats_game(cache_dir: Path, record: dict) -> dict | None:
    path = cache_dir / f"stats-{record.get('session_id')}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for game in document.get("games") or []:
        if isinstance(game, dict) and game.get("id") == record.get("game_id"):
            return game
    return None


def _save(record: dict, raw: bytes, import_dir: Path, cache_dir: Path, database) -> dict:
    stats_path = cache_dir / f"stats-{record.get('session_id')}.json"
    stats_document = None
    if stats_path.is_file():
        try:
            stats_document = json.loads(stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stats_document = None
    _save_local(import_dir, record, raw, stats_document)
    message = "Saved. Quality is at least 7.0 and the acceptance checks passed."
    if database == "auto":
        note = _save_database(import_dir, int(record["game_id"]))
        if note:
            message = f"{message} {note}"
    elif callable(database):
        database(import_dir, int(record["game_id"]))
    return {
        "status": "ready",
        "storage": "saved",
        "message": message,
        "game_id": int(record["game_id"]),
        "quality_score": record.get("quality_score"),
    }


def _save_database(import_dir: Path, game_id: int) -> str:
    from import_tiger import connect, redact

    try:
        connection = connect()
    except SystemExit as exc:
        return str(exc)
    try:
        commit_saved_match(import_dir, game_id, connection)
    except Exception as exc:
        lines = str(exc).splitlines() or [""]
        return f"The local replay was saved, but the database write failed: {redact(lines[-1])}"
    finally:
        connection.close()
    return "It was written to the database."
