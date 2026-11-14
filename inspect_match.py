#!/usr/bin/env python3
"""Print a short structural report for one downloaded telemetry payload.

The top-level match object is a standalone final state. It is not a series
entry and is not treated as an earlier snapshot. Nested arrays are counted
and displayed only as structure; they are not decoded as patches.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from download_match import MANIFEST_FILENAME, RAW_FILENAME, sha256_file

PLAYER_HINTS = {
    "pos",
    "angles",
    "currentHealth",
    "maxHealth",
    "shieldHealth",
    "shieldMaxHealth",
    "name",
    "nucleusHash",
    "status",
    "teamPlacement",
}
HEALTH_KEYS = ("currentHealth", "maxHealth")
SHIELD_KEYS = ("shieldHealth", "shieldMaxHealth")
OUTCOME_KEYS = (
    "status",
    "teamPlacement",
    "kills",
    "knockdowns",
    "revives",
    "damageDealt",
    "damageTaken",
    "survialTime",
)
PLAYER_FIELD_KEYS = HEALTH_KEYS + SHIELD_KEYS + OUTCOME_KEYS + ("pos", "angles")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect one downloaded telemetry payload.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory written by download_match.py (default: data)",
    )
    parser.add_argument("--file", type=Path, help="Raw payload path (default: the milestone file)")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path (default: the manifest beside --data-dir)",
    )
    return parser.parse_args(argv)


def load_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})."
        ) from exc


def load_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read manifest {path}: {exc}") from exc
    return payload


def kind(value) -> str:
    if isinstance(value, list):
        if not value:
            return "list[0]"
        return f"list[{len(value)} of {type(value[0]).__name__}]"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def looks_like_player(value) -> bool:
    return isinstance(value, dict) and any(key in value for key in PLAYER_HINTS)


def iter_player_objects(players):
    """Yield player-shaped objects. List wrappers are not interpreted."""
    nodes = []
    if isinstance(players, dict):
        nodes.append(players)
    elif isinstance(players, list):
        nodes.extend(item for item in players if isinstance(item, dict))
    for node in nodes:
        if looks_like_player(node):
            yield node
            continue
        for value in node.values():
            if isinstance(value, dict) and looks_like_player(value):
                yield value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and looks_like_player(item):
                        yield item


def clip(value, depth: int = 0):
    if depth >= 4:
        return "..."
    if isinstance(value, dict):
        items = list(value.items())
        trimmed = {}
        for index, (key, inner) in enumerate(items):
            if index >= 6:
                trimmed["..."] = f"{len(items) - 6} more keys"
                break
            trimmed[str(key)] = clip(inner, depth + 1)
        return trimmed
    if isinstance(value, list):
        if not value:
            return []
        if len(value) == 1:
            return [clip(value[0], depth + 1)]
        return [clip(value[0], depth + 1), f"... {len(value) - 1} more"]
    if isinstance(value, str) and len(value) > 48:
        return value[:45] + "..."
    return value


def example_text(entry: dict) -> tuple[str, bool]:
    raw = json.dumps(entry, ensure_ascii=False, separators=(", ", ": "))
    if len(raw) <= 500:
        return raw, False
    text = json.dumps(clip(entry), ensure_ascii=False, separators=(", ", ": "))
    if len(text) > 500:
        text = text[:497] + "..."
    return text, True


def record(stats: dict[str, dict], key: str, value, location: str) -> None:
    bucket = stats.setdefault(key, {"count": 0, "types": Counter(), "where": Counter()})
    bucket["count"] += 1
    bucket["types"][kind(value)] += 1
    bucket["where"][location] += 1


def scan_player(
    player: dict,
    location: str,
    stats: dict,
    pos_shapes: Counter,
    list_lengths: Counter,
    nested_pos: Counter,
) -> None:
    for key, value in player.items():
        if isinstance(value, list):
            list_lengths[len(value)] += 1
        if key in PLAYER_FIELD_KEYS:
            record(stats, key, value, location)
        if key == "pos" and isinstance(value, dict):
            pos_shapes[tuple(value.keys())] += 1
        elif key == "lastAttacker" and isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if nested_key in PLAYER_FIELD_KEYS or nested_key == "pos":
                    record(stats, nested_key, nested_value, location + ".lastAttacker")
                if nested_key == "pos" and isinstance(nested_value, dict):
                    nested_pos[tuple(nested_value.keys())] += 1


def format_types(types: Counter) -> str:
    return ", ".join(f"{name} x{count}" for name, count in types.most_common())


def format_where(where: Counter) -> str:
    return ", ".join(f"{name} x{count}" for name, count in where.most_common())


def print_field(label: str, key: str, stats: dict) -> None:
    bucket = stats.get(key)
    if not bucket:
        print(f"  {label}: not found")
        return
    print(f"  {label}: {format_types(bucket['types'])}")
    print(f"    where: {format_where(bucket['where'])}")


def scalar(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, bool)) or value is None:
        return json.dumps(value)
    return kind(value)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw_path = args.file or (args.data_dir / "raw" / RAW_FILENAME)
    manifest_path = args.manifest or (args.data_dir / "manifests" / MANIFEST_FILENAME)
    if not raw_path.exists():
        raise SystemExit(f"No payload at {raw_path}. Run download_match.py first.")

    manifest = load_manifest(manifest_path)
    digest = sha256_file(raw_path)
    if manifest and manifest.get("sha256") and manifest["sha256"] != digest:
        raise SystemExit("Payload SHA-256 does not match the manifest. Refusing to inspect it.")

    payload = load_json(raw_path)
    print("Apex WinLab telemetry inspection")
    print(f"file: {raw_path.as_posix()}")
    print(f"bytes: {raw_path.stat().st_size}")
    print(f"sha256: {digest}")
    if manifest:
        print(f"source_url: {manifest.get('source_url', '(missing)')}")
        print(f"retrieved_at: {manifest.get('retrieved_at', '(missing)')}")
    else:
        print("manifest: not found")

    print()
    print("Final state is not an earlier snapshot.")
    print("Top-level `match`, when present, is a standalone object beside `series`.")
    print("It is not a `series` entry, it is not placed on `t`, and it is not replayed.")

    print()
    if isinstance(payload, dict):
        parts = []
        for key, value in payload.items():
            if isinstance(value, dict):
                parts.append(f"{key} (object, {len(value)} keys)")
            elif isinstance(value, list):
                parts.append(f"{key} (array, {len(value)})")
            else:
                parts.append(f"{key} ({type(value).__name__})")
        print("Top-level keys: " + ", ".join(parts))
    else:
        print(f"Top-level type: {type(payload).__name__}")
        print("No object keys. Timeline fields below are omitted.")
        return 0

    match = payload.get("match")
    series = payload.get("series")
    print()
    print("Standalone match object:")
    if not isinstance(match, dict):
        print("  `match` is missing or is not an object. Unknown where the final state lives.")
    else:
        print("  keys: " + ", ".join(match.keys()))
        print("  players key on match: " + ("yes" if "players" in match else "no"))
        for key in ("state", "totalTeams", "teamsAlive", "match_start", "game_start"):
            if key in match:
                print(f"  {key}: {scalar(match[key])}")
        ring = match.get("ring")
        if isinstance(ring, dict):
            print(
                "  ring: "
                + ", ".join(f"{key}={scalar(ring[key])}" for key in list(ring)[:6])
            )
        rings = match.get("rings")
        if isinstance(rings, list):
            ring_keys = sorted(rings[0].keys()) if rings and isinstance(rings[0], dict) else []
            print(f"  rings: {len(rings)} objects; keys: {', '.join(ring_keys) or '(none)'}")
            centers = []
            for item in rings:
                if not isinstance(item, dict):
                    continue
                for center_key in ("startCenter", "endCenter"):
                    center = item.get(center_key)
                    if isinstance(center, dict):
                        centers.append(tuple(center.keys()))
            if centers:
                unique = sorted({keys for keys in centers})
                rendered = ", ".join("{" + ", ".join(keys) + "}" for keys in unique)
                print(f"  ring centers have keys {rendered}. These are ring fields, not player position.")
        config = match.get("serverConfig")
        if isinstance(config, dict):
            for key in ("map", "playlistName"):
                if key in config:
                    print(f"  serverConfig.{key}: {scalar(config[key])}")
        print("  match_start and game_start units are unknown.")

    print()
    print("Timeline (`series`, the only top-level array of objects with `t`):")
    if not isinstance(series, list):
        print("  `series` is missing or is not an array. No timeline reported.")
        print("  Event types: not identifiable from a series array.")
        return 0

    entries = [item for item in series if isinstance(item, dict)]
    print(f"  entries: {len(series)}")
    if len(entries) != len(series):
        print(f"  non-object entries: {len(series) - len(entries)}")
    times = [item.get("t") for item in entries if "t" in item]
    if times:
        print(f"  first t: {times[0]!r}")
        print(f"  last t: {times[-1]!r}")
        numeric = [value for value in times if isinstance(value, (int, float))]
        if numeric and (min(numeric) != times[0] or max(numeric) != times[-1]):
            print(f"  min t: {min(numeric)!r}")
            print(f"  max t: {max(numeric)!r}")
        ordered = all(
            isinstance(times[i], (int, float))
            and isinstance(times[i + 1], (int, float))
            and times[i] <= times[i + 1]
            for i in range(len(times) - 1)
        )
        print(f"  t order: {'non-decreasing' if ordered else 'not sorted'}")
        print(f"  distinct t values: {len(set(map(repr, times)))}")
    else:
        print("  no `t` values found on series entries")
    print("  unit of t: unknown")
    key_counts = Counter(key for item in entries for key in item)
    print("  entry keys: " + ", ".join(f"{key} x{count}" for key, count in key_counts.most_common()))

    d_keys = Counter()
    player_containers = Counter()
    series_outcome_kinds = Counter()
    list_lengths: Counter = Counter()
    for item in entries:
        body = item.get("d")
        if not isinstance(body, dict):
            continue
        d_keys.update(body.keys())
        if "players" in body:
            value = body["players"]
            if isinstance(value, list):
                player_containers[f"array[{len(value)}]"] += 1
            elif isinstance(value, dict):
                player_containers["object"] += 1
            else:
                player_containers[type(value).__name__] += 1
        for key in ("state", "teamsAlive", "totalTeams"):
            if key not in body:
                continue
            value = body[key]
            if isinstance(value, list):
                series_outcome_kinds[f"array[{len(value)}]"] += 1
            else:
                series_outcome_kinds[type(value).__name__] += 1
        for value in body.values():
            if isinstance(value, list):
                list_lengths[len(value)] += 1
    if d_keys:
        print("  d keys: " + ", ".join(f"{key} x{count}" for key, count in d_keys.most_common()))
    if player_containers:
        rendered = ", ".join(f"{name} x{count}" for name, count in player_containers.most_common())
        print(f"  d.players containers: {rendered}. Arrays here are not interpreted.")

    def entry_size(item: dict) -> tuple[int, float]:
        marker = item.get("t")
        order = marker if isinstance(marker, (int, float)) else 0
        return (len(json.dumps(item, separators=(",", ":"))), order)

    shortest = min(entries, key=entry_size)
    shown, truncated = example_text(shortest)
    label = "truncated" if truncated else "complete; it is the smallest entry"
    print(f"  example: one entry, t={shortest.get('t')!r} ({label})")
    print("  " + shown)
    print("  Arrays inside that entry are not interpreted.")

    state_note = []
    if entries:
        first_state = entries[0].get("d", {}).get("state") if isinstance(entries[0].get("d"), dict) else None
        last_state = entries[-1].get("d", {}).get("state") if isinstance(entries[-1].get("d"), dict) else None
        if first_state is not None:
            state_note.append(f"series first d.state={first_state!r}")
        if last_state is not None:
            state_note.append(f"series last d.state={last_state!r}")
    if isinstance(match, dict) and "state" in match:
        state_note.append(f"match.state={match['state']!r}")
    if state_note:
        print("  state values side by side (not aligned in time): " + "; ".join(state_note))

    event_counts: Counter = Counter()
    event_fields: dict[str, set[str]] = {}
    f_items = 0
    f_entries = 0
    stats: dict[str, dict] = {}
    pos_shapes: Counter = Counter()
    nested_pos: Counter = Counter()
    status_strings: set[str] = set()

    for item in entries:
        events = item.get("f")
        if isinstance(events, list) and events:
            f_entries += 1
        if isinstance(events, list):
            for event in events:
                f_items += 1
                if not isinstance(event, dict):
                    event_counts["(non-object)"] += 1
                    continue
                event_type = event.get("type")
                label = event_type if isinstance(event_type, str) else "(missing type)"
                event_counts[label] += 1
                event_fields.setdefault(label, set()).update(str(key) for key in event)
                for side in ("player", "victim"):
                    scan_target = event.get(side)
                    if isinstance(scan_target, dict):
                        scan_player(scan_target, "series.f", stats, pos_shapes, list_lengths, nested_pos)
                team = event.get("team")
                if isinstance(team, list):
                    for member in team:
                        if isinstance(member, dict):
                            scan_player(
                                member, "series.f.team", stats, pos_shapes, list_lengths, nested_pos
                            )
        body = item.get("d")
        if isinstance(body, dict) and "players" in body:
            for player in iter_player_objects(body["players"]):
                scan_player(player, "series.d.players", stats, pos_shapes, list_lengths, nested_pos)
                status = player.get("status")
                if isinstance(status, str):
                    status_strings.add(status)

    print()
    print("Event types:")
    print("  Source: string field `type` on objects in `series[].f`.")
    print(f"  f items: {f_items} across {f_entries} entries with a non-empty f")
    if event_counts:
        for name, count in event_counts.most_common():
            fields = ", ".join(sorted(event_fields.get(name, ())))
            print(f"  {name}: {count}" + (f"  fields seen: {fields}" if fields else ""))
    else:
        print("  not identifiable; no `type` values found")
    print("  What `d` and `f` mean relative to each other is unknown.")

    print()
    print("Player position, health, shields, outcomes:")
    print("  Found on player-shaped objects inside `series`, not on top-level `match`.")
    if pos_shapes:
        rendered = ", ".join(
            "{" + ", ".join(keys) + "}" + f" x{count}"
            for keys, count in pos_shapes.most_common()
        )
        print(f"  pos: {rendered}")
        print("    Missing coordinates are left as missing. They are not filled in.")
    else:
        print("  pos: not found")
    if nested_pos:
        print("  pos also appears under lastAttacker. That object is not interpreted.")
    angles = stats.get("angles")
    if angles:
        print(f"  angles: present ({format_where(angles['where'])}). Not treated as position.")
    for key in HEALTH_KEYS:
        print_field(key, key, stats)
    for key in SHIELD_KEYS:
        print_field(key, key, stats)
    print("  Outcome-like names (arrays are not decoded, and nothing here is totaled):")
    outcome_found = [key for key in OUTCOME_KEYS if key in stats]
    if outcome_found:
        print("    player fields: " + ", ".join(outcome_found))
        print("    location: series.d.players. Values are scalars or lists and are not totaled.")
    if isinstance(match, dict):
        print("    match.state, teamsAlive, and totalTeams are scalars on the standalone object above.")
    series_outcome = [key for key in ("state", "teamsAlive", "totalTeams") if d_keys.get(key)]
    if series_outcome:
        kinds = ", ".join(f"{name} x{count}" for name, count in series_outcome_kinds.most_common())
        print("    series d also includes: " + ", ".join(series_outcome) + f" ({kinds}).")
        print("    Those series values are not interpreted and are not the standalone match object.")
    if status_strings:
        print(
            "    scalar status strings seen: "
            + ", ".join(sorted(status_strings))
            + ". These are not a final scoreboard."
        )
    if list_lengths:
        rendered = ", ".join(f"length {length} x{count}" for length, count in sorted(list_lengths.items()))
        print(f"  Direct list lengths under d values and player fields: {rendered}.")
    print("  Those lists are unknown. They are not read as patches, replacements, or before/after pairs.")
    print("  `observers` appears inside some `d` objects. Its meaning is unknown.")
    print("  The series entry at the first t is not assumed to be the opening moment of the match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
