#!/usr/bin/env python3
"""Train and evaluate a per-checkpoint winner model on accepted games.

The model is a conditional logit: at one checkpoint of one game, each
standing team gets a score ``w . x`` and the win probabilities are the
softmax over those teams. Exactly one team is the label.

Evaluation is leave-one-game-out. Every checkpoint of a game sits in the
same fold, so no game is both trained on and scored. Imputation and scaling
are fit on the training fold only.

Below ``--min-games`` independent games the script inventories the data,
writes a report with ``status: insufficient_data`` and no metrics, and exits
nonzero. It never trains or evaluates on fewer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from features import FEATURES, build_dataset, load_accepted

# Default bar for training and evaluation. With fewer games, one game moves
# accuracy by more than 5 points and the held-out estimate is noise.
MIN_GAMES = 20
# The flag can lower the bar for experiments, never below this.
MIN_GAMES_FLOOR = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fit_scaler(groups: list[dict]) -> dict:
    """Training-fold mean and standard deviation of each known feature value."""
    scaler = {}
    for name in FEATURES:
        values = [
            row["features"][name]
            for group in groups
            for row in group["rows"]
            if row["features"][name] is not None
        ]
        mean = sum(values) / len(values) if values else 0.0
        var = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        std = math.sqrt(var)
        scaler[name] = {"mean": mean, "std": std if std > 1e-12 else 1.0}
    return scaler


def vectorize(row: dict, scaler: dict) -> list[float]:
    """Standardized features. An unknown value becomes the training mean (0)."""
    vector = []
    for name in FEATURES:
        value = row["features"][name]
        if value is None:
            vector.append(0.0)
        else:
            vector.append((value - scaler[name]["mean"]) / scaler[name]["std"])
    return vector


def softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    exps = [math.exp(score - top) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def _dot(weights: list[float], vector: list[float]) -> float:
    return sum(w * x for w, x in zip(weights, vector))


def fit(groups: list[dict], l2: float = 0.1, steps: int = 500, rate: float = 0.5) -> dict:
    """Full-batch gradient descent on the mean conditional-logit loss plus L2."""
    scaler = fit_scaler(groups)
    data = [([vectorize(row, scaler) for row in group["rows"]], group["label"]) for group in groups]
    weights = [0.0] * len(FEATURES)
    for _ in range(steps):
        grad = [l2 * w for w in weights]
        for vectors, label in data:
            probs = softmax([_dot(weights, vector) for vector in vectors])
            for vector, prob in zip(vectors, probs):
                for j, x in enumerate(vector):
                    grad[j] += prob * x / len(data)
            for j, x in enumerate(vectors[label]):
                grad[j] -= x / len(data)
        weights = [w - rate * g for w, g in zip(weights, grad)]
    return {"features": list(FEATURES), "weights": weights, "scaler": scaler, "l2": l2, "steps": steps}


def predict(model: dict, group: dict) -> list[float]:
    vectors = [vectorize(row, model["scaler"]) for row in group["rows"]]
    return softmax([_dot(model["weights"], vector) for vector in vectors])


def uniform_baseline(group: dict) -> list[float]:
    return [1.0 / len(group["rows"])] * len(group["rows"])


def players_baseline(group: dict) -> list[float]:
    """All probability on the teams with the most standing players, split evenly."""
    counts = [
        row["features"]["players_alive"] + row["features"]["players_downed"]
        for row in group["rows"]
    ]
    best = max(counts)
    tied = counts.count(best)
    return [1.0 / tied if count == best else 0.0 for count in counts]


def score(probs: list[float], label: int) -> dict:
    """Log loss, and top-1 credit split evenly across tied top scores."""
    top = max(probs)
    tied = [i for i, prob in enumerate(probs) if prob == top]
    return {
        "log_loss": -math.log(max(probs[label], 1e-15)),
        "top1": (1.0 / len(tied)) if label in tied else 0.0,
    }


def _summarize(scored: list[dict]) -> dict:
    if not scored:
        return {"n": 0}
    out = {"n": len(scored)}
    for method in ("model", "uniform", "most_players"):
        out[method] = {
            "log_loss": sum(item[method]["log_loss"] for item in scored) / len(scored),
            "top1": sum(item[method]["top1"] for item in scored) / len(scored),
        }
    return out


def leave_one_game_out(groups: list[dict], **fit_args) -> dict:
    games = sorted({group["game_id"] for group in groups}, key=str)
    scored = []
    for held in games:
        train = [group for group in groups if group["game_id"] != held]
        test = [group for group in groups if group["game_id"] == held]
        model = fit(train, **fit_args)
        for group in test:
            scored.append(
                {
                    "game_id": held,
                    "t": group["t"],
                    "checkpoint": group["checkpoint"],
                    "teams": len(group["rows"]),
                    "model": score(predict(model, group), group["label"]),
                    "uniform": score(uniform_baseline(group), group["label"]),
                    "most_players": score(players_baseline(group), group["label"]),
                }
            )
    by_checkpoint = {}
    for index in sorted({item["checkpoint"] for item in scored}):
        by_checkpoint[str(index)] = _summarize([item for item in scored if item["checkpoint"] == index])
    return {
        "method": "leave_one_game_out",
        "folds": len(games),
        "overall": _summarize(scored),
        "by_checkpoint": by_checkpoint,
    }


def _number_checkpoints(groups: list[dict]) -> None:
    """Checkpoint index within its game, 0 at the start of Playing."""
    by_game: dict = {}
    for group in groups:
        by_game.setdefault(group["game_id"], []).append(group)
    for items in by_game.values():
        times = sorted(item["t"] for item in items)
        for item in items:
            item["checkpoint"] = times.index(item["t"])


def run(data_dir: Path, out_dir: Path, min_games: int, checkpoints: int, **fit_args) -> dict:
    if min_games < MIN_GAMES_FLOOR:
        raise SystemExit(f"--min-games cannot be below {MIN_GAMES_FLOOR}")
    records, inventory = load_accepted(data_dir)
    report = {
        "generated_at": _now(),
        "data_dir": data_dir.as_posix(),
        "min_games": min_games,
        "inventory": inventory,
        "evaluation": None,
        "model_path": None,
    }
    if len(records) < min_games:
        report["status"] = "insufficient_data"
        report["detail"] = f"{len(records)} usable accepted games; {min_games} required"
        return report

    groups, dropped = build_dataset(records, checkpoints)
    _number_checkpoints(groups)
    games = sorted({group["game_id"] for group in groups}, key=str)
    report["dataset"] = {
        "games_with_rows": len(games),
        "checkpoint_groups": len(groups),
        "team_rows": sum(len(group["rows"]) for group in groups),
        "dropped_games": dropped,
        "sessions": sorted({str(group.get("session_id")) for group in groups}),
    }
    if len(games) < min_games:
        report["status"] = "insufficient_data"
        report["detail"] = f"{len(games)} games produced labeled rows; {min_games} required"
        return report

    report["evaluation"] = leave_one_game_out(groups, **fit_args)
    model = fit(groups, **fit_args)
    model["trained_on_games"] = games
    model["checkpoints_per_game"] = checkpoints
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.json"
    model_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    report["model_path"] = model_path.as_posix()
    report["status"] = "trained"
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--min-games", type=int, default=MIN_GAMES)
    parser.add_argument("--checkpoints", type=int, default=3, help="Playing checkpoints per game (default 3)")
    parser.add_argument("--l2", type=float, default=0.1)
    parser.add_argument("--steps", type=int, default=500)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(
        args.data_dir,
        args.out_dir,
        args.min_games,
        args.checkpoints,
        l2=args.l2,
        steps=args.steps,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    inventory = report["inventory"]
    print(
        f"manifest {'found' if inventory['manifest_exists'] else 'missing'}: "
        f"{inventory['games_in_manifest']} games, {inventory['accepted_in_manifest']} accepted, "
        f"{inventory['usable']} usable"
    )
    print(f"status: {report['status']}" + (f" ({report['detail']})" if report.get("detail") else ""))
    if report["evaluation"]:
        overall = report["evaluation"]["overall"]
        for method in ("model", "most_players", "uniform"):
            print(
                f"  {method:12s} log_loss {overall[method]['log_loss']:.3f} "
                f"top1 {overall[method]['top1']:.3f}  (n={overall['n']})"
            )
    print(f"report: {report_path.as_posix()}")
    return 0 if report["status"] == "trained" else 2


if __name__ == "__main__":
    sys.exit(main())
