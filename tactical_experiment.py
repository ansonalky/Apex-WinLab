#!/usr/bin/env python3
"""Do elevation, observed fire pressure, and ring access improve on condition?

Development sessions only: the frozen train and validation sessions (45 games).
The six frozen test games never enter. Preprocessing is fit inside
``train_model.fit`` on that fold's training sessions. Squads and timestamps
from one session stay together.

This does not write ``data/models``, does not change the frozen report, and
does not export predictions for the dashboard.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import train_model
from features import build_dataset, load_accepted
from tactical_features import FEATURE_SETS, REPRESENTATION, attach

BOOTSTRAP = 2000
SEED = 20261114


def development_records(records: list[dict], config: dict) -> list[dict]:
    test_sessions = {int(session) for session in config["sessions"]["test"]}
    test_games = {int(game) for game in config["games"]["test"]}
    kept = [
        record
        for record in records
        if int(record["session_id"]) not in test_sessions and int(record["game_id"]) not in test_games
    ]
    leaked = test_games & {int(record["game_id"]) for record in kept}
    if leaked:
        raise SystemExit(f"frozen test games reached development: {sorted(leaked)}")
    return kept


def session_scores(items: list[dict]) -> dict:
    """One log loss and Brier per session: mean of that session's game means."""
    games: dict = {}
    for item in items:
        slot = games.setdefault((item["session_id"], item["game_id"]), [])
        slot.append(item["constrained"])
    sessions: dict = {}
    for (session, _game), rows in games.items():
        sessions.setdefault(session, []).append(
            {
                "log_loss": sum(row["log_loss"] for row in rows) / len(rows),
                "brier": sum(row["brier"] for row in rows) / len(rows),
            }
        )
    out = {}
    for session, rows in sessions.items():
        out[session] = {
            "games": len(rows),
            "log_loss": sum(row["log_loss"] for row in rows) / len(rows),
            "brier": sum(row["brier"] for row in rows) / len(rows),
        }
    return out


def paired(reference: dict, other: dict, rng: random.Random, metric: str) -> dict:
    """``other - reference`` by session. Negative means other has the lower loss."""
    sessions = sorted(set(reference) & set(other), key=str)
    diffs = [other[session][metric] - reference[session][metric] for session in sessions]
    if not diffs:
        return {"sessions": 0}
    means = []
    for _ in range(BOOTSTRAP):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {
        "sessions": len(diffs),
        "mean_diff": sum(diffs) / len(diffs),
        "ci95": [means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]],
        "sessions_better": sum(1 for diff in diffs if diff < 0),
        "sessions_worse": sum(1 for diff in diffs if diff > 0),
        "sessions_tied": sum(1 for diff in diffs if diff == 0),
        "includes_zero": means[int(0.025 * BOOTSTRAP)] <= 0 <= means[int(0.975 * BOOTSTRAP) - 1],
    }


def score_fold(train: list[dict], held: list[dict], l2: float) -> dict:
    scored = {}
    for name, features in FEATURE_SETS.items():
        model = train_model.fit(train, l2=l2, features=features)
        items = train_model.score_groups(model, held)
        for item in items:
            item["constrained"] = item["model_masked_diagnostic"]
        scored[name] = {"model": model, "items": items}
    return scored


def arm_summary(scored: dict) -> dict:
    return {name: session_scores(payload["items"]) for name, payload in scored.items()}


def compare(summaries: dict, rng: random.Random) -> dict:
    base = summaries["corrected_condition"]
    out = {}
    for name, scores in summaries.items():
        if name == "corrected_condition":
            continue
        out[name] = {
            "log_loss": paired(base, scores, rng, "log_loss"),
            "brier": paired(base, scores, rng, "brier"),
        }
    return out


def means(scores: dict) -> dict:
    if not scores:
        return {}
    return {
        "sessions": len(scores),
        "log_loss": sum(row["log_loss"] for row in scores.values()) / len(scores),
        "brier": sum(row["brier"] for row in scores.values()) / len(scores),
    }


def failures(summaries: dict, limit: int = 5) -> list[dict]:
    base = summaries["corrected_condition"]
    combined = summaries["combined"]
    rows = []
    for session in base:
        if session not in combined:
            continue
        rows.append(
            {
                "session_id": session,
                "games": base[session]["games"],
                "condition_log_loss": base[session]["log_loss"],
                "combined_log_loss": combined[session]["log_loss"],
                "combined_minus_condition": combined[session]["log_loss"] - base[session]["log_loss"],
            }
        )
    rows.sort(key=lambda row: -row["combined_minus_condition"])
    return rows[:limit]


def elevation_weight(model: dict) -> float | None:
    if "elev_mean_dz" not in model["features"]:
        return None
    return model["weights"][model["features"].index("elev_mean_dz")]


def run(data_dir: Path, config_path: Path) -> dict:
    started = time.perf_counter()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    l2 = config["l2"]
    records, inventory = load_accepted(data_dir)
    if not inventory.get("manifest_exists"):
        raise SystemExit(f"manifest missing under {data_dir}")
    records = development_records(records, config)
    groups, dropped, _diagnostics = build_dataset(records, data_dir)
    present_games = {int(group["game_id"]) for group in groups}
    if set(config["games"]["test"]) & present_games:
        raise SystemExit("a frozen test game produced a development checkpoint")
    feature_started = time.perf_counter()
    groups, coverage = attach(groups, records, data_dir)
    feature_seconds = time.perf_counter() - feature_started
    train_sessions = {int(session) for session in config["sessions"]["train"]}
    val_sessions = {int(session) for session in config["sessions"]["validation"]}
    rng = random.Random(SEED)
    fit_started = time.perf_counter()
    split = score_fold(
        [group for group in groups if int(group["session_id"]) in train_sessions],
        [group for group in groups if int(group["session_id"]) in val_sessions],
        l2,
    )
    loso_items = {name: [] for name in FEATURE_SETS}
    for held in sorted(train_sessions | val_sessions):
        fold = score_fold(
            [group for group in groups if int(group["session_id"]) != held],
            [group for group in groups if int(group["session_id"]) == held],
            l2,
        )
        for name, payload in fold.items():
            loso_items[name].extend(payload["items"])
    fit_seconds = time.perf_counter() - fit_started
    split_scores = arm_summary(split)
    loso_scores = {name: session_scores(items) for name, items in loso_items.items()}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment": "tactical features against a corrected condition baseline",
        "question": "Do elevation, observed fire pressure, and revealed ring access improve prediction beyond squad condition?",
        "deployed": False,
        "replaces_current_predictions": False,
        "frozen_l2": l2,
        "l2_note": "Taken from config/frozen_evaluation.json. Not re-selected.",
        "representation": REPRESENTATION,
        "feature_sets": {name: list(features) for name, features in FEATURE_SETS.items()},
        "development_games": len(present_games),
        "development_sessions": len({int(group["session_id"]) for group in groups}),
        "checkpoints": len(groups),
        "frozen_test_games_excluded": list(config["games"]["test"]),
        "dropped_games": dropped,
        "coverage": coverage,
        "timing_s": {
            "features": round(feature_seconds, 1),
            "fits": round(fit_seconds, 1),
            "total": round(time.perf_counter() - started, 1),
        },
        "metrics": "Session mean of game-mean log loss and Brier, on eligibility-constrained probabilities. Lower is better.",
        "train_to_validation": {
            "sessions": means(split_scores["corrected_condition"])["sessions"] if split_scores["corrected_condition"] else 0,
            "levels": {name: means(scores) for name, scores in split_scores.items()},
            "minus_corrected_condition": compare(split_scores, rng),
        },
        "leave_one_session_out": {
            "sessions": len(loso_scores["corrected_condition"]),
            "levels": {name: means(scores) for name, scores in loso_scores.items()},
            "minus_corrected_condition": compare(loso_scores, rng),
            "worst_combined_sessions": failures(loso_scores),
        },
        "elevation_weight_standardized": {
            "train_fit_condition_elevation": elevation_weight(split["condition_elevation"]["model"]),
            "train_fit_combined": elevation_weight(split["combined"]["model"]),
            "note": "Weight on standardized elev_mean_dz. Not constrained to be positive.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/frozen_evaluation.json"))
    parser.add_argument("--out", type=Path, default=Path("experiments/tactical-model/report.json"))
    args = parser.parse_args(argv)
    report = run(args.data_dir, args.config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"games {report['development_games']} checkpoints {report['checkpoints']} coverage {report['coverage']}")
    print(f"timing {report['timing_s']}")
    for label in ("train_to_validation", "leave_one_session_out"):
        block = report[label]
        print(label)
        for name, level in block["levels"].items():
            print(f"  {name:24s} log_loss {level['log_loss']:.4f}  brier {level['brier']:.4f}")
        for name, diffs in block["minus_corrected_condition"].items():
            loss = diffs["log_loss"]
            print(
                f"  {name} log_loss diff {loss['mean_diff']:+.4f} "
                f"CI {loss['ci95'][0]:+.4f} to {loss['ci95'][1]:+.4f} "
                f"better {loss['sessions_better']} worse {loss['sessions_worse']} "
                f"zero_in_ci {loss['includes_zero']}"
            )
    print(f"report {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
