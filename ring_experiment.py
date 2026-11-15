#!/usr/bin/env python3
"""Position-feature v2 experiment: does ring-relative position help?

Compares, with the frozen ``l2`` and no tuning:

- ``condition``: status counts and health (``train_model.CONDITION_FEATURES``)
- ``condition_ring``: condition + ``ring_features.RING_FEATURES``
- ``condition_v1pos``: the frozen v1 feature set, for reference

on train and validation sessions only. The frozen test sessions (the six
already-inspected games) are excluded and asserted absent. Two evaluations:

1. fit on train sessions, score validation sessions (9 games);
2. leave-one-session-out over all train + validation sessions (45 games).

Scores use the eligibility-constrained probabilities (verified-eliminated
squads exactly 0). Results go to ``data/experiments/ring-v2/report.json``.
The frozen v1 model, bundles, and reports are not touched.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import train_model
from features import FEATURES, build_dataset, load_accepted
from ring_features import NEARBY_RING_RADII, RING_FEATURES, ring_snapshots

FEATURE_SETS = {
    "condition": tuple(train_model.CONDITION_FEATURES),
    "condition_ring": tuple(train_model.CONDITION_FEATURES) + RING_FEATURES,
    "condition_v1pos": tuple(FEATURES),
}
BOOTSTRAP = 2000
SEED = 20261114


def attach_ring_features(groups: list[dict], records: list[dict]) -> dict:
    """Add ring features to each row (copies), and count what was known."""
    by_game: dict = {}
    for group in groups:
        by_game.setdefault(group["game_id"], []).append(group)
    raw = {record["game_id"]: record["raw_path"] for record in records}
    coverage = {"rows": 0, "geometry_known": 0, "squad_position_known": 0, "phases": {}}
    for game_id, items in by_game.items():
        series = json.loads(Path(raw[game_id]).read_text(encoding="utf-8-sig"))["series"]
        snaps = ring_snapshots(series, {group["t"] for group in items})
        for group in items:
            snap = snaps[group["t"]]
            phase = snap["ring"]["phase"]
            coverage["phases"][phase] = coverage["phases"].get(phase, 0) + 1
            rows = []
            for row in group["rows"]:
                extra = snap["squads"].get(row["team"]) or {name: None for name in RING_FEATURES} | {
                    "ring_geometry_missing": 1.0,
                    "ring_squad_position_missing": 1.0,
                }
                coverage["rows"] += 1
                coverage["geometry_known"] += extra["ring_geometry_missing"] == 0.0
                coverage["squad_position_known"] += extra["ring_center_dist_rel"] is not None
                rows.append({**row, "features": {**row["features"], **extra}})
            group["rows"] = rows
    return coverage


def score(model: dict, groups: list[dict]) -> list[dict]:
    items = train_model.score_groups(model, groups)
    for item in items:
        item["constrained"] = item["model_masked_diagnostic"]
    return items


def per_game(items: list[dict], method: str) -> dict:
    games: dict = {}
    for item in items:
        games.setdefault(item["game_id"], []).append(item[method]["log_loss"])
    return {game: sum(values) / len(values) for game, values in games.items()}


def summary(items: list[dict]) -> dict:
    out = {}
    for method in ("constrained", "model", "uniform_contention"):
        games = per_game(items, method)
        out[method] = sum(games.values()) / len(games)
    return out


def paired(a: dict, b: dict, rng: random.Random) -> dict:
    """``b - a`` per game (negative = b better), with a game bootstrap interval."""
    games = sorted(a, key=str)
    diffs = [b[g] - a[g] for g in games]
    means = []
    for _ in range(BOOTSTRAP):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {
        "games": len(diffs),
        "mean_diff": sum(diffs) / len(diffs),
        "ci95": [means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]],
        "games_better": sum(1 for d in diffs if d < 0),
        "games_worse": sum(1 for d in diffs if d > 0),
    }


def evaluate(train: list[dict], test: list[dict], l2: float) -> dict:
    results = {}
    for name, features in FEATURE_SETS.items():
        model = train_model.fit(train, l2=l2, features=features)
        if not model["optimizer"]["converged"]:
            raise SystemExit(f"{name} did not converge")
        results[name] = score(model, test)
    return results


def run(data_dir: Path, config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    test_sessions = set(config["sessions"]["test"])
    test_games = set(config["games"]["test"])
    l2 = config["l2"]
    records, _ = load_accepted(data_dir)
    records = [r for r in records if r.get("session_id") not in test_sessions]
    groups, dropped, _diag = build_dataset(records, data_dir)
    if test_games & {g["game_id"] for g in groups}:
        raise SystemExit("a frozen test game reached the experiment")
    coverage = attach_ring_features(groups, records)
    train_sessions = set(config["sessions"]["train"])
    val_sessions = set(config["sessions"]["validation"])
    rng = random.Random(SEED)

    split = evaluate(
        [g for g in groups if g["session_id"] in train_sessions],
        [g for g in groups if g["session_id"] in val_sessions],
        l2,
    )
    sessions = sorted(train_sessions | val_sessions, key=str)
    loso: dict = {name: [] for name in FEATURE_SETS}
    for held in sessions:
        fold = evaluate([g for g in groups if g["session_id"] != held], [g for g in groups if g["session_id"] == held], l2)
        for name, items in fold.items():
            loso[name].extend(items)

    def block(results: dict) -> dict:
        games = {name: per_game(items, "constrained") for name, items in results.items()}
        return {
            "games": len(games["condition"]),
            "log_loss_game_weighted": {name: summary(items) for name, items in results.items()},
            "ring_minus_condition": paired(games["condition"], games["condition_ring"], rng),
            "v1pos_minus_condition": paired(games["condition"], games["condition_v1pos"], rng),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment": "position-feature v2 (ring-relative)",
        "frozen_test_sessions_excluded": sorted(test_sessions, key=str),
        "l2": l2,
        "l2_source": "frozen evaluation config; not tuned here",
        "nearby_threshold_ring_radii": NEARBY_RING_RADII,
        "metric": "game-weighted log loss of eligibility-constrained probabilities (lower is better)",
        "feature_sets": {name: list(features) for name, features in FEATURE_SETS.items()},
        "dropped_games": dropped,
        "coverage": coverage,
        "train_to_validation": block(split),
        "leave_one_session_out": block(loso),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("config/frozen_evaluation.json"))
    parser.add_argument("--out", type=Path, default=Path("data/experiments/ring-v2/report.json"))
    args = parser.parse_args(argv)
    report = run(args.data_dir, args.config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    cov = report["coverage"]
    print(f"squad rows {cov['rows']}: ring geometry known {cov['geometry_known']}, squad ring position known {cov['squad_position_known']}")
    print(f"checkpoint phases: {cov['phases']}")
    for label in ("train_to_validation", "leave_one_session_out"):
        block = report[label]
        print(f"{label} ({block['games']} games):")
        for name, values in block["log_loss_game_weighted"].items():
            print(f"  {name:16s} constrained {values['constrained']:.4f}  raw {values['model']:.4f}  contention {values['uniform_contention']:.4f}")
        for key in ("ring_minus_condition", "v1pos_minus_condition"):
            d = block[key]
            print(f"  {key}: {d['mean_diff']:+.4f} nats (95% CI {d['ci95'][0]:+.4f} to {d['ci95'][1]:+.4f}), better in {d['games_better']}, worse in {d['games_worse']}")
    print(f"report: {args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
