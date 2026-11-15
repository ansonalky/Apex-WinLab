"""Expand the accepted-game set without touching the frozen dataset.

The frozen manifest is read only. New games, caches, and telemetry go under
a different data directory. Acceptance still requires quality >= 7, a
validated historical replay, and a verified winner. Games already in the
frozen manifest are duplicates and are not downloaded again.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from collect_matches import (
    TERMINAL,
    BudgetExceeded,
    Client,
    TemporaryFailure,
    Unavailable,
    is_rate_limited,
    _apply_result,
    _cache_json,
    _index_known,
    _iso,
    _print_game,
    _remember_duplicate,
    _search_page,
    _session_id,
    catalog_summary,
    discover_session,
    download_gate,
    fetch_telemetry,
    load_manifest,
    merge_records,
    save_manifest,
)
def _day_from_epoch(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _day_from_iso(value) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


def preferred_maps(frozen: dict, limit: int = 2) -> list[str]:
    """The maps with the most accepted games in the frozen dataset."""
    counts: Counter[str] = Counter()
    for row in frozen.get("games", {}).values():
        if row.get("reason") == "accepted" and row.get("map_name"):
            counts[row["map_name"]] += 1
    return [name for name, _count in counts.most_common(limit)]


def frozen_index(frozen: dict) -> dict:
    game_ids = set()
    sessions = set()
    lives = {}
    mids = {}
    accepted = 0
    for row in frozen.get("games", {}).values():
        game_id = row.get("game_id")
        if game_id is not None:
            game_ids.add(game_id)
        if row.get("session_id") is not None:
            sessions.add(row.get("session_id"))
        if row.get("livedata_id"):
            lives[row["livedata_id"]] = game_id
        if row.get("mid"):
            mids[row["mid"]] = game_id
        if row.get("reason") == "accepted":
            accepted += 1
    return {
        "game_ids": game_ids,
        "sessions": sessions,
        "lives": lives,
        "mids": mids,
        "accepted": accepted,
    }


def mark_frozen_duplicate(record: dict, index: dict) -> bool:
    """True when this game is already in the frozen dataset."""
    game_id = record.get("game_id")
    livedata = record.get("livedata_id")
    mid = record.get("mid")
    if game_id in index["game_ids"]:
        record["reason"] = "duplicate"
        record["acceptance_status"] = "excluded"
        record["duplicate_of"] = game_id
        record["inconsistent_mapping"] = "already in the frozen dataset"
        return True
    if livedata and livedata in index["lives"]:
        record["reason"] = "duplicate"
        record["acceptance_status"] = "excluded"
        record["duplicate_of"] = index["lives"][livedata]
        record["inconsistent_mapping"] = f"livedata already frozen as game {index['lives'][livedata]}"
        return True
    if mid and mid in index["mids"] and index["mids"][mid] != game_id:
        record["reason"] = "duplicate"
        record["acceptance_status"] = "excluded"
        record["duplicate_of"] = index["mids"][mid]
        record["inconsistent_mapping"] = f"mid already frozen as game {index['mids'][mid]}"
        return True
    return False


def select_downloads(
    eligible: list[dict],
    preferred: set[str],
    per_session: int,
    other_slots: int,
) -> tuple[list[dict], dict[int, str]]:
    """Choose which eligible games to download.

    Preferred maps come first, at most ``per_session`` games. A session that
    has no preferred-map candidate may use one of the remaining other-map
    slots. Everyone else is labeled and not downloaded.
    """
    preferred_rows = [row for row in eligible if row.get("map_name") in preferred]
    other_rows = [row for row in eligible if row.get("map_name") not in preferred]
    chosen = list(preferred_rows[:per_session])
    if not preferred_rows and other_slots > 0:
        chosen.extend(other_rows[: min(per_session, other_slots)])
    chosen_ids = {id(row) for row in chosen}
    skipped: dict[int, str] = {}
    for row in eligible:
        if id(row) in chosen_ids:
            continue
        if row.get("map_name") in preferred:
            reason = "session_cap"
        elif preferred_rows:
            reason = "map_not_preferred"
        elif other_slots <= 0:
            reason = "other_map_cap"
        else:
            reason = "session_cap"
        skipped[id(row)] = reason
    return chosen, skipped


def _accepted_by_day(document: dict) -> Counter:
    counts: Counter = Counter()
    for row in document.get("games", {}).values():
        if row.get("reason") != "accepted":
            continue
        day = _day_from_iso(row.get("match_start_utc"))
        if day:
            counts[day] += 1
    return counts


def _other_accepted(document: dict, preferred: set[str]) -> int:
    return sum(
        1
        for row in document.get("games", {}).values()
        if row.get("reason") == "accepted" and row.get("map_name") not in preferred
    )


def collect_expansion(
    data_dir: Path,
    frozen_dir: Path,
    *,
    min_quality: float = 7.0,
    max_sessions: int = 1000,
    accepted_target_total: int = 200,
    request_budget: int = 2000,
    delay: float = 0.2,
    timeout: float = 120.0,
    retries: int = 3,
    max_per_session: int = 2,
    max_per_day: int = 8,
    max_examined_per_day: int = 8,
    other_map_cap: int = 40,
) -> dict:
    data_dir = data_dir.resolve()
    frozen_dir = frozen_dir.resolve()
    if data_dir == frozen_dir:
        raise SystemExit("expansion data dir must be separate from the frozen dataset")
    frozen = load_manifest(frozen_dir)
    index = frozen_index(frozen)
    preferred = preferred_maps(frozen)
    if len(preferred) < 1:
        raise SystemExit("frozen dataset has no accepted maps to prefer")
    needed = max(0, accepted_target_total - index["accepted"])
    client = Client(timeout=timeout, retries=retries, delay=delay, budget=request_budget)
    cache = data_dir / "collection" / "cache"
    document = load_manifest(data_dir)
    document.setdefault("games", {})
    discovery = document.setdefault("discovery", {})
    discovery["mode"] = "expansion"
    discovery["frozen_dir"] = str(frozen_dir)
    discovery["frozen_accepted"] = index["accepted"]
    discovery["target_total"] = accepted_target_total
    discovery["preferred_maps"] = preferred
    discovery.setdefault("next_start", 0)
    discovery.setdefault("sessions_examined", 0)
    discovery.setdefault("examined_by_day", {})
    discovery.setdefault("skipped", {"frozen_session": 0, "date_examined_cap": 0, "date_accept_cap": 0})
    next_start = int(discovery.get("next_start") or 0)
    limits: list[str] = []
    stop_note = ""
    new_downloads = 0
    validated: list[dict] = []
    lives, mids = _index_known(document)
    lives.update({key: value for key, value in index["lives"].items() if key not in lives})
    mids.update({key: value for key, value in index["mids"].items() if key not in mids})

    def accepted_now() -> int:
        return sum(1 for row in document["games"].values() if row.get("reason") == "accepted")

    def finish(reason: str = "") -> dict:
        if reason and reason not in limits:
            limits.append(reason)
        discovery["limits_reached"] = limits
        discovery["stop_note"] = stop_note
        discovery["next_start"] = next_start
        document["min_quality"] = min_quality
        save_manifest(data_dir, document)
        summary = catalog_summary(document, new_downloads, limits, client, min_quality)
        summary["stop_note"] = stop_note
        summary["frozen_accepted"] = index["accepted"]
        summary["target_total"] = accepted_target_total
        summary["unique_total"] = index["accepted"] + summary["accepted"]
        summary["preferred_maps"] = preferred
        summary["needed"] = needed
        summary["skipped"] = discovery.get("skipped")
        return summary

    if needed == 0:
        stop_note = "frozen dataset already has the requested number of accepted games"
        return finish("already_complete")

    preferred_set = set(preferred)
    probe = accepted_now() == 0
    # A previous run may have queued sessions after HTTP 429. Keep the ids, do not request them.
    queued = discovery.pop("retry_session_ids", None) or []
    if queued:
        kept = discovery.setdefault("rate_limited_session_ids", [])
        for session_id in queued:
            if session_id not in kept:
                kept.append(session_id)
    discovery["automatic_retries"] = "disabled"
    while int(discovery.get("sessions_examined") or 0) < max_sessions and accepted_now() < needed:
        try:
            page = _search_page(cache, client, next_start, "", "")
        except BudgetExceeded:
            return finish("request_budget")
        except (TemporaryFailure, Unavailable) as exc:
            if is_rate_limited(exc):
                stop_note = f"stopped on HTTP 429: {exc}"
                return finish("rate_limited")
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
        exhausted = bool(total) and next_start + len(page_rows) >= total
        for row in page_rows:
            if int(discovery.get("sessions_examined") or 0) >= max_sessions or accepted_now() >= needed:
                break
            session_id = _session_id(row)
            next_start += 1
            discovery["next_start"] = next_start
            if session_id is None:
                continue
            if session_id in index["sessions"]:
                discovery["skipped"]["frozen_session"] += 1
                continue
            day = _day_from_epoch(row.get("start"))
            examined_by_day = discovery.setdefault("examined_by_day", {})
            if day and int(examined_by_day.get(day) or 0) >= max_examined_per_day:
                discovery["skipped"]["date_examined_cap"] += 1
                continue
            if day and _accepted_by_day(document)[day] >= max_per_day:
                discovery["skipped"]["date_accept_cap"] += 1
                continue
            discovery["sessions_examined"] = int(discovery.get("sessions_examined") or 0) + 1
            if day:
                examined_by_day[day] = int(examined_by_day.get(day) or 0) + 1
            try:
                records, meta = discover_session(session_id, cache, client, row)
            except BudgetExceeded:
                return finish("request_budget")
            except (TemporaryFailure, Unavailable, OSError, json.JSONDecodeError) as exc:
                if is_rate_limited(exc):
                    stop_note = f"stopped on HTTP 429: {exc}"
                    return finish("rate_limited")
                print(f"session {session_id} skipped {exc}")
                save_manifest(data_dir, document)
                continue
            except SystemExit as exc:
                print(f"session {session_id} skipped {exc}")
                continue
            for record in records:
                if mark_frozen_duplicate(record, index):
                    continue
                _remember_duplicate(record, lives, mids)
            merged = merge_records(document, records, min_quality)
            eligible = []
            for record in merged:
                if record.get("reason") in TERMINAL and record.get("failure_class") != "temporary":
                    if record.get("livedata_id"):
                        lives[record["livedata_id"]] = record.get("game_id")
                    continue
                gate = download_gate(record, min_quality)
                if gate:
                    record["acceptance_status"] = "excluded"
                    record["reason"] = gate
                    continue
                eligible.append(record)
            other_slots = other_map_cap - _other_accepted(document, preferred_set)
            chosen, skipped = select_downloads(eligible, preferred_set, max_per_session, other_slots)
            chosen_ids = {id(item) for item in chosen}
            for record in eligible:
                if id(record) in chosen_ids:
                    continue
                record["acceptance_status"] = "excluded"
                record["reason"] = skipped.get(id(record), "session_cap")
            print(
                f"session {session_id} {row.get('eventId') or '-'} day {day or '-'} "
                f"games {len(merged)} eligible {len(eligible)} download {len(chosen)}"
            )
            for record in chosen:
                if accepted_now() >= needed:
                    break
                before = record.get("download_status")
                try:
                    fetched = fetch_telemetry(record, data_dir, client)
                except BudgetExceeded:
                    save_manifest(data_dir, document)
                    return finish("request_budget")
                if fetched.get("failure_class") == "temporary" and "429" in str(fetched.get("error")):
                    stop_note = f"stopped on HTTP 429: {fetched.get('error')}"
                    save_manifest(data_dir, document)
                    return finish("rate_limited")
                if before in (None, "not_attempted", "failed_temporary") and fetched.get("download_status") == "downloaded":
                    new_downloads += 1
                _apply_result(record, fetched, min_quality, data_dir)
                if record.get("livedata_id"):
                    lives[record["livedata_id"]] = record.get("game_id")
                validated.append(record)
                _print_game(record)
                save_manifest(data_dir, document)
                if probe and len(validated) == 3 and not any(item.get("reason") == "accepted" for item in validated[:3]):
                    reasons = [item.get("reason") for item in validated[:3]]
                    stop_note = "first three qualifying games failed validation: " + ", ".join(str(item) for item in reasons)
                    return finish("probe_failed")
            save_manifest(data_dir, document)
        if exhausted:
            return finish("search_exhausted")
    if accepted_now() >= needed:
        return finish("accepted_target")
    return finish("max_sessions")


def print_expansion(summary: dict) -> None:
    print(
        f"frozen {summary.get('frozen_accepted')} "
        f"new accepted {summary.get('accepted')} "
        f"unique total {summary.get('unique_total')} "
        f"target {summary.get('target_total')}"
    )
    print(
        f"examined sessions {summary.get('sessions_examined')} "
        f"requests {summary.get('requests')} "
        f"preferred {', '.join(summary.get('preferred_maps') or [])}"
    )
    print("skipped " + ", ".join(f"{key}={value}" for key, value in sorted((summary.get("skipped") or {}).items())))
    print("by reason " + ", ".join(f"{key}={value}" for key, value in sorted((summary.get("by_reason") or {}).items())))
    print("limits " + ", ".join(summary.get("limits_reached") or ["none"]))
    if summary.get("stop_note"):
        print(summary["stop_note"])


def squad_format(stats_game: dict | None) -> str:
    """Format from the longest recorded roster. A short squad does not change it."""
    if not isinstance(stats_game, dict):
        return "unknown"
    lengths = []
    for team in stats_game.get("teams") or []:
        players = team.get("player_stats") if isinstance(team, dict) else None
        if isinstance(players, list) and players:
            lengths.append(len(players))
    if not lengths:
        return "unknown"
    size = max(lengths)
    if size >= 3:
        return "trios"
    if size == 2:
        return "duos"
    if size == 1:
        return "solos"
    return "unknown"


def _load_payload(record: dict, data_dir: Path) -> dict | None:
    raw = record.get("download_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = data_dir.parent / path if not path.exists() else path
    if not path.exists():
        path = data_dir / "raw" / f"{record.get('livedata_id')}.timeseries.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


_POI_KEY_TOKENS = ("poi", "droppoint", "droplocation", "landmark", "locationname", "areaname", "zonename")


def poi_key_names(payload: dict) -> list[str]:
    """Key names that look like a drop-point identifier. Values are not interpreted."""
    found: set[str] = set()

    def walk(obj, depth: int = 0) -> None:
        if depth > 6 or obj is None:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                lowered = str(key).lower()
                if any(token in lowered for token in _POI_KEY_TOKENS):
                    found.add(str(key))
                walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:8]:
                walk(item, depth + 1)

    walk(payload.get("match"))
    for entry in (payload.get("series") or [])[:30]:
        if isinstance(entry, dict):
            walk(entry.get("d"))
    return sorted(found)


def early_position_sample(payload: dict) -> dict | None:
    """Positions at Playing start and 20 seconds later. No POI names are assigned."""
    from statistics import pstdev

    from overstat_patch import playing_bounds, replay

    series = payload.get("series")
    if not isinstance(series, list):
        return None
    pre = replay(series)
    bounds = playing_bounds(pre["state_names"])
    if bounds is None:
        return {"playing": False}
    start = bounds[0]
    out = replay(series, {start, start + 20})

    def measure(t: int) -> dict:
        rows = out["snapshots"].get(t) or []
        xs = []
        fresh = 0
        missing = 0
        for row in rows:
            pos = (row.get("pos") or {}).get("value") or {}
            gap = ((row.get("pos") or {}).get("gap") or {}).get("x")
            if pos.get("x") is None or pos.get("y") is None:
                missing += 1
                continue
            xs.append(float(pos["x"]))
            if gap == 0:
                fresh += 1
        spread = pstdev(xs) if len(xs) > 1 else 0.0
        return {
            "players": len(rows),
            "with_xy": len(xs),
            "missing": missing,
            "fresh_x": fresh,
            "stdev_x": round(spread, 1),
        }

    at_start = measure(start)
    at_20 = measure(start + 20)
    covered = (
        at_20["players"] > 0
        and at_20["with_xy"] / at_20["players"] >= 0.8
        and at_20["stdev_x"] >= 5000
    )
    return {"playing_t": start, "at_start": at_start, "at_20s": at_20, "spread_by_20s": covered}


def _stats_game(data_dir: Path, record: dict) -> dict | None:
    path = data_dir / "collection" / "cache" / f"stats-{record.get('session_id')}.json"
    if not path.exists():
        return None
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    matches = [
        game
        for game in (stats.get("games") or [])
        if isinstance(game, dict) and game.get("id") == record.get("game_id")
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _accepted_rows(document: dict) -> list[dict]:
    return [row for row in document.get("games", {}).values() if row.get("reason") == "accepted"]


def _breakdown(rows: list[dict], data_dir: Path | None) -> dict:
    by_map: Counter = Counter()
    by_session: Counter = Counter()
    by_date: Counter = Counter()
    by_format: Counter = Counter()
    for row in rows:
        by_map[row.get("map_name") or "unknown"] += 1
        by_session[str(row.get("session_id"))] += 1
        by_date[_day_from_iso(row.get("match_start_utc")) or "unknown"] += 1
        if data_dir is None:
            by_format["not_counted"] += 1
            continue
        by_format[squad_format(_stats_game(data_dir, row))] += 1
    return {
        "games": len(rows),
        "by_map": dict(by_map.most_common()),
        "by_session": dict(by_session.most_common()),
        "by_date": dict(sorted(by_date.items())),
        "by_format": dict(by_format.most_common()),
        "sessions": len(by_session),
        "dates": len([day for day in by_date if day != "unknown"]),
    }


def build_report(data_dir: Path, frozen_dir: Path, *, sample_limit: int | None = None) -> dict:
    """Summarize the separate expansion. Does not modify either dataset."""
    data_dir = data_dir.resolve()
    frozen_dir = frozen_dir.resolve()
    frozen = load_manifest(frozen_dir)
    expansion = load_manifest(data_dir)
    frozen_rows = _accepted_rows(frozen)
    new_rows = _accepted_rows(expansion)
    frozen_ids = {row.get("game_id") for row in frozen_rows}
    overlap = [row.get("game_id") for row in new_rows if row.get("game_id") in frozen_ids]
    reasons = Counter((row.get("reason") or "pending") for row in expansion.get("games", {}).values())
    poi_names: Counter = Counter()
    early = {"sampled": 0, "spread_by_20s": 0, "missing_payload": 0, "no_playing": 0}
    start_stacked = 0
    samples = new_rows if sample_limit is None else new_rows[:sample_limit]
    for row in samples:
        payload = _load_payload(row, data_dir)
        if payload is None:
            early["missing_payload"] += 1
            continue
        for name in poi_key_names(payload):
            poi_names[name] += 1
        measured = early_position_sample(payload)
        if not measured or measured.get("playing") is False:
            early["no_playing"] += 1
            continue
        early["sampled"] += 1
        if measured.get("spread_by_20s"):
            early["spread_by_20s"] += 1
        at_start = measured.get("at_start") or {}
        if at_start.get("stdev_x", 0) < 1000 and at_start.get("with_xy", 0) > 1:
            start_stacked += 1
    early["stacked_at_playing_start"] = start_stacked
    discovery = expansion.get("discovery") or {}
    return {
        "frozen_accepted": len(frozen_rows),
        "expansion_accepted": len(new_rows),
        "unique_total": len(frozen_rows) + len(new_rows) - len(overlap),
        "overlap_with_frozen": overlap,
        "preferred_maps": discovery.get("preferred_maps") or preferred_maps(frozen),
        "target_total": discovery.get("target_total"),
        "limits_reached": discovery.get("limits_reached"),
        "stop_note": discovery.get("stop_note"),
        "sessions_examined": discovery.get("sessions_examined"),
        "skipped": discovery.get("skipped"),
        "requests_note": "Request counts are per process. Resume adds to the same manifest.",
        "exclusion_reasons": dict(reasons.most_common()),
        "frozen": _breakdown(frozen_rows, frozen_dir),
        "expansion": _breakdown(new_rows, data_dir),
        "early_positions": early,
        "poi_identifier_keys": dict(poi_names.most_common()),
        "poi_note": (
            "No key matching a drop-point or POI identifier was found."
            if not poi_names
            else "Keys whose names match a drop-point token. This is not a POI boundary."
        ),
    }


def render_markdown(report: dict) -> str:
    expansion = report["expansion"]
    frozen = report["frozen"]
    early = report["early_positions"]
    sampled = early.get("sampled") or 0
    spread = early.get("spread_by_20s") or 0
    lines = [
        "# Dataset expansion",
        "",
        "The frozen production manifest was not modified. New games are stored only under the expansion data directory.",
        "",
        f"Frozen accepted games: {report['frozen_accepted']}.",
        f"New accepted games: {report['expansion_accepted']}.",
        f"Unique total: {report['unique_total']}.",
        f"Target: {report.get('target_total')}.",
        f"Overlap with the frozen game ids: {len(report.get('overlap_with_frozen') or [])}.",
        f"Preferred maps, from frozen accepted counts: {', '.join(report.get('preferred_maps') or [])}.",
        f"Sessions examined in the expansion: {report.get('sessions_examined')}.",
        f"Stop: {', '.join(report.get('limits_reached') or ['none'])}. {report.get('stop_note') or ''}",
        "",
        "## New games",
        "",
        f"Sessions {expansion['sessions']}, dates {expansion['dates']}.",
        "",
        "Maps:",
    ]
    for name, count in expansion["by_map"].items():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("Dates:")
    for day, count in expansion["by_date"].items():
        lines.append(f"- {day}: {count}")
    lines.append("")
    lines.append("Squad format, from the longest `player_stats` roster in that game's standings:")
    for name, count in expansion["by_format"].items():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("Sessions:")
    for name, count in expansion["by_session"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Frozen games (unchanged)", ""])
    lines.append(f"Sessions {frozen['sessions']}, dates {frozen['dates']}.")
    lines.append("")
    lines.append("Maps:")
    for name, count in frozen["by_map"].items():
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append("Dates:")
    for day, count in frozen["by_date"].items():
        lines.append(f"- {day}: {count}")
    lines.append("")
    lines.append("Squad format:")
    for name, count in frozen["by_format"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Exclusions in the expansion manifest", ""])
    for name, count in report["exclusion_reasons"].items():
        lines.append(f"- {name}: {count}")
    skipped = report.get("skipped") or {}
    if skipped:
        lines.append("")
        lines.append("Sessions skipped before download:")
        for name, count in sorted(skipped.items()):
            lines.append(f"- {name}: {count}")
    lines.extend(["", "## Early positions and drop points", ""])
    lines.append(
        f"Sampled {sampled} new accepted replays. "
        f"{spread} have positions for at least 80% of players and an x standard deviation of at least 5000 "
        "at 20 seconds after Playing starts."
    )
    lines.append(
        f"{early.get('stacked_at_playing_start', 0)} of those samples are still stacked "
        "(x standard deviation under 1000) at the first Playing frame."
    )
    lines.append(report["poi_note"])
    if report["poi_identifier_keys"]:
        for name, count in report["poi_identifier_keys"].items():
            lines.append(f"- key `{name}` in {count} sampled replays")
    else:
        lines.append("Player rows include `pos` x/y/z and `spawnTime`. Neither is a POI id or a loot rating.")
    lines.append("No POI boundary or loot quality was inferred.")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_report(data_dir: Path, frozen_dir: Path, markdown_path: Path, json_path: Path) -> dict:
    report = build_report(data_dir, frozen_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return report
