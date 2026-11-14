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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from collection import (
    QUALITY_SCOPE,
    QUALITY_SOURCE,
    SESSION_QUALITY_SOURCE,
    decide,
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


class Client:
    def __init__(self, timeout: float, retries: int, sleep=time.sleep):
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def get(self, url: str) -> bytes:
        last: BaseException | None = None
        for attempt in range(1, self.retries + 1):
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
        return json.loads(path.read_text(encoding="utf-8"))
    body = client.get(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return _load_json_bytes(body)


def _iso(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def discover_session(session_id: int, cache: Path, client: Client) -> tuple[list[dict], dict]:
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
    event = stats.get("eventId") if isinstance(stats, dict) else None
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


def _apply_result(record: dict, fetched: dict, minimum: float) -> None:
    record.update({key: value for key, value in fetched.items() if key != "payload"})
    payload = fetched.get("payload")
    validation = None
    if fetched.get("invalid"):
        validation = {"ok": False, "reason": "invalid_payload", "detail": fetched["invalid"]}
        record["validation_status"] = "failed"
    elif payload is not None:
        validation = validate_timeseries(payload)
        record["validation_status"] = "passed" if validation.get("ok") else "failed"
        record["validation"] = {key: value for key, value in validation.items() if key != "ok"}
    elif record.get("download_status") == "cached":
        path = Path(fetched["download_path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            validation = validate_timeseries(payload)
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


def run_collection(
    session_id: int,
    data_dir: Path,
    client: Client,
    minimum: float,
    limit_games: int,
    max_downloads: int,
) -> dict:
    cache = data_dir / "collection" / "cache"
    records, meta = discover_session(session_id, cache, client)
    records = records[:limit_games]
    document = load_manifest(data_dir)
    merged = merge_records(document, records, minimum)
    downloads = 0
    for record in merged:
        if record.get("reason") == "duplicate" or not record.get("livedata_id"):
            status, reason = decide(record, None, minimum)
            record["acceptance_status"] = status
            record["reason"] = reason
            continue
        if record.get("reason") in TERMINAL and record.get("failure_class") != "temporary" and record.get("download_status") not in (None, "not_attempted", "failed_temporary"):
            continue
        if downloads >= max_downloads and record.get("download_status") in (None, "not_attempted", "failed_temporary"):
            status, reason = decide(record, record.get("validation"), minimum)
            if record.get("reason") != "duplicate":
                record["acceptance_status"] = status
                record["reason"] = reason or record.get("reason") or ""
            continue
        if record.get("download_status") == "cached" and record.get("validation_status") in ("passed", "failed") and record.get("failure_class") != "temporary":
            status, reason = decide(record, {"ok": record.get("validation_status") == "passed", "reason": record.get("validation", {}).get("reason")}, minimum)
            record["acceptance_status"] = status
            record["reason"] = reason
            continue
        before = record.get("download_status")
        fetched = fetch_telemetry(record, data_dir, client)
        if before in (None, "not_attempted", "failed_temporary") and fetched.get("download_status") == "downloaded":
            downloads += 1
        _apply_result(record, fetched, minimum)
        save_manifest(data_dir, document)
    for record in merged:
        if not record.get("reason"):
            status, reason = decide(record, None, minimum)
            record["acceptance_status"] = status
            record["reason"] = reason
    document["session"] = meta
    document["games"] = {str(row.get("game_id")): row for row in merged}
    save_manifest(data_dir, document)
    return summarize(merged, meta, downloads)


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
    parser.add_argument("--session", type=int, default=23231, help="Tournament session id (default: 23231)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--min-quality", type=float, default=7.0)
    parser.add_argument("--limit-games", type=int, default=10)
    parser.add_argument("--max-downloads", type=int, default=None)
    parser.add_argument("--accepted-target", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "preview":
        max_downloads = 0
    elif args.command == "trial":
        max_downloads = 3 if args.max_downloads is None else args.max_downloads
    elif args.command == "resume":
        max_downloads = 3 if args.max_downloads is None else args.max_downloads
    else:
        max_downloads = args.max_downloads if args.max_downloads is not None else 10**9
    if args.command == "run":
        print(
            "run stops at the accepted target for this one session. "
            "It does not crawl the site. The 50-game target needs more sessions later.",
            file=sys.stderr,
        )
    client = Client(timeout=args.timeout, retries=args.retries)
    summary = run_collection(
        args.session,
        args.data_dir,
        client,
        args.min_quality,
        args.limit_games,
        max_downloads,
    )
    # run_collection already printed nothing. Reload records for the short list.
    document = load_manifest(args.data_dir)
    listed = [document["games"][key] for key in document["games"]]
    print_summary(listed, summary)
    if args.command == "run" and summary["accepted"] >= args.accepted_target:
        print(f"accepted target {args.accepted_target} reached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
