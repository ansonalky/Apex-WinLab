#!/usr/bin/env python3
"""Reconstruct historical player state with Overstat's patchData rules.

The standalone ``match`` object is written apart from the checkpoints. It is
the final payload object, not a frame on ``t``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from download_match import RAW_FILENAME
from overstat_patch import default_checkpoints, frame_entries, replay


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay one timeseries and export three active-play snapshots."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory used by download_match.py (default: data)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Raw payload path (default: the downloaded sample)",
    )
    parser.add_argument(
        "--t",
        dest="checkpoints",
        type=int,
        action="append",
        help="Checkpoint t. Repeat for each. Default: start, middle, and end of Playing.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output JSON (default: DATA_DIR/derived/checkpoints.json)",
    )
    return parser.parse_args(argv)


def load_payload(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise SystemExit("Payload must be an object with a series array.")
    return payload


def event_clock_mismatches(series: list[dict], match_start: int) -> int:
    mismatches = 0
    for entry in series:
        t = entry.get("t")
        for event in entry.get("f") or []:
            if not isinstance(event, dict) or "timestamp" not in event:
                continue
            try:
                absolute = int(event["timestamp"])
            except (TypeError, ValueError):
                mismatches += 1
                continue
            if not isinstance(t, int) or absolute - match_start != t:
                mismatches += 1
    return mismatches


def final_outcomes(match: dict, series: list[dict]) -> dict:
    config = match.get("serverConfig") if isinstance(match.get("serverConfig"), dict) else {}
    omitted = series[-1] if series else None
    omitted_view = None
    if isinstance(omitted, dict):
        body = omitted.get("d")
        omitted_view = {
            "t": omitted.get("t"),
            "d": body if _compact(body) else {"keys": sorted(body) if isinstance(body, dict) else None},
            "f_count": len(omitted.get("f") or []),
        }
    return {
        "role": (
            "payload.match is the standalone final object shipped beside series. "
            "It is not a replay frame, it is not placed on t, and it is not a model input."
        ),
        "state": match.get("state"),
        "totalTeams": match.get("totalTeams"),
        "teamsAlive": match.get("teamsAlive"),
        "match_start": match.get("match_start"),
        "game_start": match.get("game_start"),
        "ring": match.get("ring"),
        "map": config.get("map"),
        "playlistName": config.get("playlistName"),
        "unapplied_series_tail": omitted_view,
    }


def _compact(value) -> bool:
    try:
        return len(json.dumps(value)) <= 500
    except TypeError:
        return False


def checkpoint_document(payload: dict, checkpoints: list[int], result: dict) -> dict:
    match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
    series = payload["series"]
    match_start = _maybe_int(match.get("match_start"))
    game_start = _maybe_int(match.get("game_start"))
    mismatches = (
        event_clock_mismatches(series, match_start) if match_start is not None else None
    )
    frames = frame_entries(series)
    playing = [
        frame["t"]
        for frame, name in zip(frames, result["state_names"])
        if name == "Playing"
    ]
    return {
        "decoder": "overstat patchData / bundled jsondiffpatch patch",
        "initial_state": {"feed": []},
        "clock": {
            "match_start": match.get("match_start"),
            "game_start": match.get("game_start"),
            "game_start_minus_match_start": (
                None
                if match_start is None or game_start is None
                else game_start - match_start
            ),
            "t": (
                "series t is seconds after match_start. "
                "The replay page computes event time as timestamp - match_start "
                "and compares it with currentTime, which is the frame's t."
            ),
            "event_timestamp_minus_match_start_mismatches": mismatches,
        },
        "timeline": {
            "raw_entries": len(series),
            "replay_frames": result["frame_count"],
            "first_applied_t": frames[0]["t"] if frames else None,
            "last_applied_t": frames[-1]["t"] if frames else None,
            "omitted_t": result["omitted_t"],
            "playing_first_t": playing[0] if playing else None,
            "playing_last_t": playing[-1] if playing else None,
            "patch_failures": result["failures"],
        },
        "checkpoints": [
            {
                "t": t,
                "state": (
                    result["state_names"][t]
                    if 0 <= t < len(result["state_names"])
                    else None
                ),
                "reconstructed_teams_alive": result.get("checkpoint_meta", {}).get(t, {}).get("teamsAlive"),
                "reconstructed_total_teams": result.get("checkpoint_meta", {}).get(t, {}).get("totalTeams"),
                "absolute_time": None if match_start is None else match_start + t,
                "player_count": len(result["snapshots"].get(t, [])),
                "players": result["snapshots"].get(t, []),
            }
            for t in checkpoints
        ],
        "final_outcomes_not_model_input": final_outcomes(match, series),
    }


def _maybe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gap_summary(players: list[dict], group: str, field: str) -> str:
    gaps = []
    unknown = 0
    for player in players:
        if group == "pos":
            gap = player["pos"]["gap"][field]
        elif group == "status":
            gap = player["status"]["gap"]
        else:
            gap = player[group][field]["gap"]
        if gap is None:
            unknown += 1
        else:
            gaps.append(gap)
    fresh = sum(1 for gap in gaps if gap == 0)
    carried = sum(1 for gap in gaps if gap > 0)
    return f"fresh {fresh}, carried {carried}, unknown {unknown}"


def print_summary(document: dict, out: Path) -> None:
    clock = document["clock"]
    timeline = document["timeline"]
    print("Apex WinLab historical reconstruction")
    print(f"wrote: {out.as_posix()}")
    print(
        f"frames applied: {timeline['replay_frames']} "
        f"(t {timeline['first_applied_t']}..{timeline['last_applied_t']}); "
        f"omitted t: {timeline['omitted_t']}"
    )
    print(
        f"Playing t: {timeline['playing_first_t']}..{timeline['playing_last_t']}"
    )
    print(
        "clock: t = timestamp - match_start; "
        f"mismatches {clock['event_timestamp_minus_match_start_mismatches']}; "
        f"game_start - match_start = {clock['game_start_minus_match_start']}"
    )
    print(f"patch failures: {len(timeline['patch_failures'])}")
    final = document["final_outcomes_not_model_input"]
    print(
        "final match object kept separate: "
        f"state={final['state']!r} teamsAlive={final['teamsAlive']} "
        f"totalTeams={final['totalTeams']}"
    )
    for checkpoint in document["checkpoints"]:
        players = checkpoint["players"]
        print(
            f"checkpoint t={checkpoint['t']} state={checkpoint['state']!r} "
            f"players={checkpoint['player_count']} "
            f"teamsAlive={checkpoint['reconstructed_teams_alive']} "
            f"absolute_time={checkpoint['absolute_time']}"
        )
        print(f"  pos.x {_gap_summary(players, 'pos', 'x')}")
        print(f"  pos.y {_gap_summary(players, 'pos', 'y')}")
        print(f"  pos.z {_gap_summary(players, 'pos', 'z')}")
        print(f"  health.current {_gap_summary(players, 'health', 'current')}")
        print(f"  shields.current {_gap_summary(players, 'shields', 'current')}")
        print(f"  status {_gap_summary(players, 'status', 'status')}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw_path = args.file or (args.data_dir / "raw" / RAW_FILENAME)
    out = args.out or (args.data_dir / "derived" / "checkpoints.json")
    if not raw_path.exists():
        raise SystemExit(f"No payload at {raw_path}. Run download_match.py first.")
    payload = load_payload(raw_path)
    series = payload["series"]
    probe = replay(series)
    checkpoints = args.checkpoints or default_checkpoints(probe["state_names"])
    result = replay(series, set(checkpoints))
    document = checkpoint_document(payload, checkpoints, result)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print_summary(document, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
