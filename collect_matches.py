#!/usr/bin/env python3
"""Collect a few Overstat games and validate replay telemetry.

One HTTP request at a time. Raw bytes are cached and never overwritten.
The known trial session is 23231, the tournament on the replay page
https://overstat.gg/tournament/octanecollegiate/23231.S5_Premier_Lobby1_Week_6/replay/3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from collection import (
    QUALITY_SCOPE,
    QUALITY_SOURCE,
    SESSION_QUALITY_SOURCE,
    decide,
    download_gate,
    game_quality,
    mark_duplicates,
    session_quality,
    validate_timeseries,
)
from download_match import atomic_write, sha256_bytes, sha256_file, utc_now_iso, write_manifest

API = "https://overstat.gg/api"
USER_AGENT = "ApexWinLab/0.3 (resumable collector)"
TERMINAL = {
    "below_quality_threshold",
    "quality_unknown",
    "score_scope_unresolved",
    "replay_missing",
    "invalid_payload",
    "reconstruction_failed",
    "insufficient_position_data",
    "outcome_unknown",
    "duplicate",
    "accepted",
}


class TemporaryFailure(Exception):
    pass


class Unavailable(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Client:
    def __init__(self, timeout: float, retries: int, sleep=time.sleep, delay: float = 0, budget: int | None = None):
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.delay = delay
        self.budget = budget
        self.used = 0
        self.budget_hit = False

    def get(self, url: str) -> bytes:
        last: BaseException | None = None
        for attempt in range(1, self.retries + 1):
            if self.budget is not None and self.used >= self.budget:
                self.budget_hit = True
                raise BudgetExceeded(f"request budget {self.budget} reached")
            if self.used and self.delay:
                self.sleep(self.delay)
            self.used += 1
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404 or exc.code == 410 or 400 <= exc.code < 500 and exc.code != 429:
                    raise Unavailable(f"HTTP {exc.code} {url}") from exc
                last = exc
                if attempt == self.retries:
                    break
                self.sleep(_delay(attempt, exc))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt == self.retries:
                    break
                self.sleep(min(2 ** (attempt - 1), 8))
        raise TemporaryFailure(f"{url}: {last}")


def _delay(attempt: int, exc: urllib.error.HTTPError) -> float:
    if exc.code == 429 and exc.headers is not None:
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
    return min(2 ** (attempt - 1), 8)


def _load_json_bytes(data: bytes) -> dict | list:
    return json.loads(data.decode("utf-8-sig"))


def _cache_json(path: Path, url: str, client: Client):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    body = client.get(url)
    atomic_write(path, body)
    return _load_json_bytes(body)


def _iso(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def search_url(start: int, count: int, name: str, organizer: str) -> str:
    params = {"start": start, "count": count, "name": name or ""}
    if organizer:
        params["organizer"] = organizer
    return f"{API}/match/search?{urllib.parse.urlencode(params)}"


def discover_session(session_id: int, cache: Path, client: Client, session_info: dict | None = None) -> tuple[list[dict], dict]:
    games_url = f"{API}/games/{session_id}"
    stats_url = f"{API}/stats/{session_id}/overall"
    games = _cache_json(cache / f"games-{session_id}.json", games_url, client)
    stats = _cache_json(cache / f"stats-{session_id}.json", stats_url, client)
    if not isinstance(games, list):
        raise SystemExit(f"/api/games/{session_id} was not a list")
    if not isinstance(stats, dict) or not isinstance(stats.get("games"), list):
        scope_unresolved = True
        stats_by_id = {}
    else:
        scope_unresolved = False
        stats_by_id = {
            item.get("id"): item for item in stats["games"] if isinstance(item, dict)
        }
    session_score = session_quality(stats if isinstance(stats, dict) else None)
    info = session_info or {}
    event = info.get("eventId")
    if event is None and isinstance(stats, dict):
        event = stats.get("eventId")
    records = []
    for game in games:
        if not isinstance(game, dict):
            continue
        stats_game = stats_by_id.get(game.get("id"))
        livedata = game.get("livedata") or None
        if livedata is not None and not isinstance(livedata, str):
            livedata = str(livedata)
        record = {
            "session_id": session_id,
            "session_api_url": games_url,
            "stats_url": stats_url,
            "event": event,
            "organizer_username": info.get("username"),
            "organizer_id": info.get("organizer"),
            "game_number": game.get("game"),
            "game_id": game.get("id"),
            "livedata_id": livedata,
            "mid": game.get("mid"),
            "map_name": game.get("map_name"),
            "match_start": game.get("match_start"),
            "match_start_utc": _iso(game.get("match_start")),
            "quality_score": None if scope_unresolved else game_quality(stats_game),
            "quality_scope": None if scope_unresolved else QUALITY_SCOPE,
            "quality_source": None if scope_unresolved else QUALITY_SOURCE.format(session_id=session_id),
            "session_quality_score": session_score,
            "session_quality_scope": "session",
            "session_quality_source": SESSION_QUALITY_SOURCE.format(session_id=session_id),
            "score_scope_unresolved": scope_unresolved,
            "download_status": "not_attempted",
            "validation_status": "not_run",
            "acceptance_status": "pending",
            "reason": "",
            "failure_class": None,
        }
        records.append(record)
    mark_duplicates(records)
    meta = {
        "session_id": session_id,
        "games_listed": len(records),
        "with_livedata": sum(1 for row in records if row.get("livedata_id")),
        "session_quality_score": session_score,
        "score_scope_unresolved": scope_unresolved,
    }
    return records, meta


def _telemetry_paths(data_dir: Path, livedata_id: str) -> tuple[Path, Path]:
    raw = data_dir / "raw" / f"{livedata_id}.timeseries.json"
    manifest = data_dir / "manifests" / f"{livedata_id}.timeseries.manifest.json"
    return raw, manifest


def fetch_telemetry(record: dict, data_dir: Path, client: Client) -> dict:
    """Download one payload. An existing file is reused and never rewritten."""
    livedata_id = record["livedata_id"]
    url = f"{API}/stats/livedata/{livedata_id}?format=timeseries"
    raw_path, manifest_path = _telemetry_paths(data_dir, livedata_id)
    if raw_path.exists():
        digest = sha256_file(raw_path)
        retrieved = None
        if manifest_path.exists():
            try:
                retrieved = json.loads(manifest_path.read_text(encoding="utf-8")).get("retrieved_at")
            except (OSError, json.JSONDecodeError):
                retrieved = None
        return {
            "download_status": "cached",
            "source_url": url,
            "download_path": raw_path.as_posix(),
            "sha256": digest,
            "retrieved_at": retrieved,
            "failure_class": None,
        }
    try:
        body = client.get(url)
    except TemporaryFailure as exc:
        return {
            "download_status": "failed_temporary",
            "source_url": url,
            "failure_class": "temporary",
            "error": str(exc),
        }
    except Unavailable as exc:
        return {
            "download_status": "unavailable",
            "source_url": url,
            "failure_class": "unavailable",
            "error": str(exc),
        }
    try:
        payload = _load_json_bytes(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        rejected = data_dir / "rejected" / f"{livedata_id}.body"
        atomic_write(rejected, body)
        return {
            "download_status": "downloaded",
            "source_url": url,
            "download_path": rejected.as_posix(),
            "sha256": sha256_bytes(body),
            "retrieved_at": utc_now_iso(),
            "invalid": str(exc),
            "failure_class": None,
        }
    atomic_write(raw_path, body)
    digest = sha256_bytes(body)
    retrieved = utc_now_iso()
    if not manifest_path.exists():
        write_manifest(
            manifest_path,
            url=url,
            retrieved_at=retrieved,
            filename=raw_path.as_posix(),
            digest=digest,
        )
    return {
        "download_status": "downloaded",
        "source_url": url,
        "download_path": raw_path.as_posix(),
        "sha256": digest,
        "retrieved_at": retrieved,
        "payload": payload,
        "failure_class": None,
    }


def stats_game_for(record: dict, data_dir: Path) -> dict | None:
    """Standings for this game id. Array position is not an identifier."""
    path = data_dir / "collection" / "cache" / f"stats-{record.get('session_id')}.json"
    if not path.exists():
        return None
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    target = record.get("game_id")
    matches = [
        game
        for game in (stats.get("games") or [])
        if isinstance(game, dict) and game.get("id") == target
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _validate_payload(payload: dict, record: dict, data_dir: Path) -> dict:
    return validate_timeseries(payload, stats_game_for(record, data_dir))


def _apply_result(record: dict, fetched: dict, minimum: float, data_dir: Path) -> None:
    record.update({key: value for key, value in fetched.items() if key != "payload"})
    payload = fetched.get("payload")
    validation = None
    if fetched.get("invalid"):
        validation = {"ok": False, "reason": "invalid_payload", "detail": fetched["invalid"]}
        record["validation_status"] = "failed"
    elif payload is not None:
        validation = _validate_payload(payload, record, data_dir)
        record["validation_status"] = "passed" if validation.get("ok") else "failed"
        record["validation"] = {key: value for key, value in validation.items() if key != "ok"}
    elif record.get("download_status") == "cached":
        path = Path(fetched["download_path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            validation = _validate_payload(payload, record, data_dir)
            record["validation_status"] = "passed" if validation.get("ok") else "failed"
            record["validation"] = {key: value for key, value in validation.items() if key != "ok"}
        except (OSError, json.JSONDecodeError) as exc:
            validation = {"ok": False, "reason": "invalid_payload", "detail": str(exc)}
            record["validation_status"] = "failed"
    status, reason = decide(record, validation, minimum)
    if record.get("reason") != "duplicate":
        record["acceptance_status"] = status
        record["reason"] = reason


def _manifest_path(data_dir: Path) -> Path:
    return data_dir / "collection" / "manifest.json"


def load_manifest(data_dir: Path) -> dict:
    path = _manifest_path(data_dir)
    if not path.exists():
        return {"version": 1, "games": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(data_dir: Path, document: dict) -> None:
    path = _manifest_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def merge_records(document: dict, records: list[dict], minimum: float) -> list[dict]:
    games = document.setdefault("games", {})
    merged = []
    for record in records:
        key = str(record.get("game_id"))
        previous = games.get(key)
        if isinstance(previous, dict) and previous.get("reason") in TERMINAL and previous.get("acceptance_status") != "pending":
            if previous.get("failure_class") != "temporary":
                merged.append(previous)
                continue
        current = dict(previous or {})
        current.update({k: v for k, v in record.items() if k not in current or current.get(k) in (None, "", "not_attempted", "not_run", "pending")})
        # Refresh quality fields from the latest stats cache, but keep download results.
        for field in (
            "quality_score",
            "quality_scope",
            "quality_source",
            "session_quality_score",
            "session_quality_scope",
            "session_quality_source",
            "score_scope_unresolved",
            "livedata_id",
            "map_name",
            "match_start",
            "match_start_utc",
            "event",
            "mid",
            "organizer_username",
            "organizer_id",
        ):
            current[field] = record.get(field)
        if record.get("reason") == "duplicate":
            current["reason"] = "duplicate"
            current["acceptance_status"] = "excluded"
            current["duplicate_of"] = record.get("duplicate_of")
            current["inconsistent_mapping"] = record.get("inconsistent_mapping")
        games[key] = current
        merged.append(current)
    document["min_quality"] = minimum
    return merged


def _session_id(row: dict) -> int | None:
    value = row.get("matchId", row.get("id"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _search_page(cache: Path, client: Client, start: int, name: str, organizer: str) -> dict:
    url = search_url(start, 20, name, organizer)
    slug = urllib.parse.quote(f"{start}-{name}-{organizer}", safe="")
    return _cache_json(cache / f"search-{slug}.json", url, client)


def _remember_duplicate(record: dict, known_live: dict, known_mid: dict) -> None:
    if record.get("reason") == "duplicate":
        return
    livedata = record.get("livedata_id")
    mid = record.get("mid")
    game_id = record.get("game_id")
    if livedata and livedata in known_live and known_live[livedata] != game_id:
        record["reason"] = "duplicate"
        record["duplicate_of"] = known_live[livedata]
        record["inconsistent_mapping"] = f"livedata {livedata} previously game {known_live[livedata]}"
        record["acceptance_status"] = "excluded"
    elif mid and mid in known_mid and known_mid[mid] != game_id:
        record["reason"] = "duplicate"
        record["duplicate_of"] = known_mid[mid]
        record["inconsistent_mapping"] = f"mid {mid} previously game {known_mid[mid]}"
        record["acceptance_status"] = "excluded"


def _index_known(document: dict) -> tuple[dict, dict]:
    lives: dict = {}
    mids: dict = {}
    for row in document.get("games", {}).values():
        if row.get("livedata_id"):
            lives[row["livedata_id"]] = row.get("game_id")
        if row.get("mid"):
            mids[row["mid"]] = row.get("game_id")
    return lives, mids


def _quality_text(score) -> str:
    return "unknown" if score is None else f"{score:.3f}"


def _print_game(record: dict) -> None:
    winner = (record.get("validation") or {}).get("winner") or {}
    print(
        f"  session {record.get('session_id')} {record.get('event')} "
        f"game {record.get('game_id')} quality {_quality_text(record.get('quality_score'))} "
        f"replay {'yes' if record.get('livedata_id') else 'no'} "
        f"{record.get('acceptance_status')} {record.get('reason')} "
        f"winner {winner.get('teamName') or '-'}"
    )


def catalog_summary(document: dict, new_downloads: int, limits: list[str], client: Client, minimum: float) -> dict:
    rows = list(document.get("games", {}).values())
    reasons: dict[str, int] = {}
    for row in rows:
        key = row.get("reason") or "pending"
        reasons[key] = reasons.get(key, 0) + 1

    def numeric(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    at_least = [row for row in rows if numeric(row.get("quality_score")) and row["quality_score"] >= minimum]
    accepted_rows = [row for row in rows if row.get("reason") == "accepted"]
    return {
        "sessions_examined": document.get("discovery", {}).get("sessions_examined"),
        "games_examined": len(rows),
        "quality_at_least_min": len(at_least),
        "qualifying_with_replay": sum(1 for row in at_least if row.get("livedata_id")),
        "new_downloads": new_downloads,
        "accepted": len(accepted_rows),
        "by_reason": reasons,
        "accepted_sessions": sorted({row.get("session_id") for row in accepted_rows}),
        "accepted_maps": sorted({row.get("map_name") for row in accepted_rows if row.get("map_name")}),
        "accepted_organizers": sorted({row.get("organizer_username") for row in accepted_rows if row.get("organizer_username")}),
        "examined_sessions": len({row.get("session_id") for row in rows}),
        "examined_maps": len({row.get("map_name") for row in rows if row.get("map_name")}),
        "examined_organizers": len({row.get("organizer_username") for row in rows if row.get("organizer_username")}),
        "limits_reached": limits,
        "requests": client.used,
        "temporary_failures": sum(1 for row in rows if row.get("failure_class") == "temporary"),
    }


def collect_catalog(
    data_dir: Path,
    client: Client,
    minimum: float,
    max_sessions: int,
    max_downloads: int | None,
    accepted_target: int,
    name: str,
    organizer: str,
    only_session: int | None,
) -> dict:
    """Walk public sessions. Download only quality-qualified new replays."""
    cache = data_dir / "collection" / "cache"
    document = load_manifest(data_dir)
    document.setdefault("games", {})
    discovery = document.setdefault("discovery", {})
    if discovery.get("name") != name or discovery.get("organizer") != organizer:
        discovery["next_start"] = 0
    next_start = 0 if only_session is not None else int(discovery.get("next_start") or 0)
    discovery["name"] = name
    discovery["organizer"] = organizer
    limits: list[str] = []
    stop_note = ""
    new_downloads = 0
    examined_now = 0
    validated: list[dict] = []
    accepted = sum(1 for row in document["games"].values() if row.get("reason") == "accepted")
    lives, mids = _index_known(document)

    def finish(reason: str = "") -> dict:
        if reason and reason not in limits:
            limits.append(reason)
        discovery["limits_reached"] = limits
        discovery["stop_note"] = stop_note
        document["min_quality"] = minimum
        save_manifest(data_dir, document)
        summary = catalog_summary(document, new_downloads, limits, client, minimum)
        summary["stop_note"] = stop_note
        summary["validated_now"] = [
            {
                "session_id": row.get("session_id"),
                "event": row.get("event"),
                "game_id": row.get("game_id"),
                "quality_score": row.get("quality_score"),
                "reason": row.get("reason"),
                "winner": (row.get("validation") or {}).get("winner"),
            }
            for row in validated[:3]
        ]
        return summary

    while int(discovery.get("sessions_examined") or 0) < max_sessions and accepted < accepted_target:
        if only_session is not None:
            page_rows = [{"matchId": only_session}]
            exhausted = True
        else:
            try:
                page = _search_page(cache, client, next_start, name, organizer)
            except BudgetExceeded:
                return finish("request_budget")
            except (TemporaryFailure, Unavailable) as exc:
                stop_note = f"search stopped: {exc}"
                return finish("search_failed")
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                stop_note = "search response had no data list"
                return finish("search_failed")
            page_rows = page["data"]
            if not page_rows:
                return finish("search_exhausted")
            try:
                total = int(page.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            exhausted = total and next_start + len(page_rows) >= total
        for row in page_rows:
            if int(discovery.get("sessions_examined") or 0) >= max_sessions or accepted >= accepted_target:
                break
            session_id = _session_id(row)
            if session_id is None:
                next_start += 1
                continue
            examined_now += 1
            next_start += 1
            discovery["next_start"] = next_start
            discovery["sessions_examined"] = int(discovery.get("sessions_examined") or 0) + 1
            try:
                records, meta = discover_session(session_id, cache, client, row)
            except BudgetExceeded:
                return finish("request_budget")
            except (TemporaryFailure, Unavailable, OSError, json.JSONDecodeError) as exc:
                print(f"session {session_id} skipped {exc}")
                save_manifest(data_dir, document)
                continue
            except SystemExit as exc:
                print(f"session {session_id} skipped {exc}")
                continue
            for record in records:
                _remember_duplicate(record, lives, mids)
            merged = merge_records(document, records, minimum)
            above = sum(
                1
                for item in merged
                if isinstance(item.get("quality_score"), (int, float)) and not isinstance(item.get("quality_score"), bool)
                and item["quality_score"] >= minimum
            )
            print(
                f"session {session_id} {row.get('eventId') or meta.get('session_id')} "
                f"organizer {row.get('username') or '-'} games {len(merged)} "
                f"quality>= {above} replay {meta.get('with_livedata')}"
            )
            for record in merged:
                if record.get("reason") in TERMINAL and record.get("failure_class") != "temporary":
                    if record.get("livedata_id"):
                        lives[record["livedata_id"]] = record.get("game_id")
                    continue
                gate = download_gate(record, minimum)
                if gate:
                    record["acceptance_status"] = "excluded"
                    record["reason"] = gate
                    continue
                if max_downloads is not None and new_downloads >= max_downloads:
                    if "max_downloads" not in limits:
                        limits.append("max_downloads")
                    break
                before = record.get("download_status")
                try:
                    fetched = fetch_telemetry(record, data_dir, client)
                except BudgetExceeded:
                    save_manifest(data_dir, document)
                    return finish("request_budget")
                if before in (None, "not_attempted", "failed_temporary") and fetched.get("download_status") == "downloaded":
                    new_downloads += 1
                _apply_result(record, fetched, minimum, data_dir)
                if record.get("livedata_id"):
                    lives[record["livedata_id"]] = record.get("game_id")
                validated.append(record)
                if record.get("reason") == "accepted":
                    accepted += 1
                _print_game(record)
                save_manifest(data_dir, document)
                if len(validated) == 3:
                    print("first three qualifying validations:")
                    for item in validated[:3]:
                        _print_game(item)
                    if not any(item.get("reason") == "accepted" for item in validated[:3]):
                        reasons = [item.get("reason") for item in validated[:3]]
                        stop_note = "first three qualifying games failed validation: " + ", ".join(reasons)
                        return finish("probe_failed")
            save_manifest(data_dir, document)
            if "max_downloads" in limits:
                return finish("max_downloads")
        if only_session is not None or exhausted:
            return finish("search_exhausted" if only_session is None else "session_done")
    if accepted >= accepted_target:
        return finish("accepted_target")
    return finish("max_sessions")


def summarize(records: list[dict], meta: dict, new_downloads: int) -> dict:
    reasons: dict[str, int] = {}
    for record in records:
        reason = record.get("reason") or "pending"
        reasons[reason] = reasons.get(reason, 0) + 1
    temporary = sum(1 for row in records if row.get("failure_class") == "temporary")
    return {
        "session_id": meta.get("session_id"),
        "candidates": len(records),
        "with_livedata": meta.get("with_livedata"),
        "session_quality_score": meta.get("session_quality_score"),
        "score_scope_unresolved": meta.get("score_scope_unresolved"),
        "new_downloads": new_downloads,
        "accepted": reasons.get("accepted", 0),
        "duplicates": reasons.get("duplicate", 0),
        "temporary_failures": temporary,
        "by_reason": reasons,
    }


def print_summary(records: list[dict], summary: dict) -> None:
    print(
        f"session {summary['session_id']}: {summary['candidates']} games, "
        f"{summary['with_livedata']} with livedata, "
        f"session quality {summary['session_quality_score']} (not used per game)"
    )
    if summary.get("score_scope_unresolved"):
        print("score scope unresolved: stats response had no per-game analytics")
    for record in records:
        quality = record.get("quality_score")
        quality_text = "unknown" if quality is None else f"{quality:.3f}"
        print(
            f"  game {record.get('game_number')} id {record.get('game_id')} "
            f"map {record.get('map_name')} quality {quality_text} "
            f"livedata {'yes' if record.get('livedata_id') else 'no'} "
            f"download {record.get('download_status')} "
            f"validation {record.get('validation_status')} "
            f"{record.get('acceptance_status')} {record.get('reason')}"
        )
    print(
        f"new downloads {summary['new_downloads']}; accepted {summary['accepted']}; "
        f"duplicates {summary['duplicates']}; temporary {summary['temporary_failures']}"
    )
    print("by reason " + ", ".join(f"{key}={value}" for key, value in sorted(summary["by_reason"].items())))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and validate Overstat games.")
    parser.add_argument("command", choices=("preview", "trial", "resume", "run"))
    parser.add_argument("--session", type=int, default=None, help="Only this session. Omit to search public sessions.")
    parser.add_argument("--name", default="", help="Event-name search. Empty searches all public sessions.")
    parser.add_argument("--organizer", default="", help="Organizer filter passed to match search.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--min-quality", type=float, default=7.0)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--max-downloads", type=int, default=None)
    parser.add_argument("--accepted-target", type=int, default=50)
    parser.add_argument("--request-budget", type=int, default=400)
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between HTTP requests.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args(argv)


def _print_catalog(summary: dict) -> None:
    print(
        f"examined sessions {summary['sessions_examined']} "
        f"games {summary['games_examined']} "
        f"quality>=min {summary['quality_at_least_min']} "
        f"with replay {summary['qualifying_with_replay']}"
    )
    print(
        f"new downloads {summary['new_downloads']} accepted {summary['accepted']} "
        f"requests {summary['requests']} temporary {summary['temporary_failures']}"
    )
    print("by reason " + ", ".join(f"{key}={value}" for key, value in sorted(summary["by_reason"].items())))
    print(
        f"accepted organizers {summary['accepted_organizers'] or '-'} "
        f"maps {summary['accepted_maps'] or '-'} "
        f"sessions {summary['accepted_sessions'] or '-'}"
    )
    print(
        f"examined distinct sessions {summary['examined_sessions']} "
        f"maps {summary['examined_maps']} organizers {summary['examined_organizers']}"
    )
    print("limits " + ", ".join(summary["limits_reached"] or ["none"]))
    if summary.get("stop_note"):
        print(summary["stop_note"])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "preview":
        max_sessions = 10 if args.max_sessions is None else args.max_sessions
        max_downloads = 0 if args.max_downloads is None else args.max_downloads
    elif args.command == "trial":
        max_sessions = 100 if args.max_sessions is None else args.max_sessions
        max_downloads = 3 if args.max_downloads is None else args.max_downloads
    else:
        max_sessions = 100 if args.max_sessions is None else args.max_sessions
        max_downloads = args.max_downloads
    client = Client(
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        budget=args.request_budget,
    )
    summary = collect_catalog(
        args.data_dir,
        client,
        args.min_quality,
        max_sessions,
        max_downloads,
        args.accepted_target,
        args.name,
        args.organizer,
        args.session,
    )
    _print_catalog(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
