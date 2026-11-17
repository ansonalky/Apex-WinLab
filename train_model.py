#!/usr/bin/env python3
"""Train and evaluate a per-checkpoint winner model on accepted games.

Model: conditional logit. At one checkpoint of one game each eligible squad
scores ``w . x`` and the win probabilities are the softmax over those squads.
The verified eventual winner is the single positive.

Weighting: each checkpoint group carries weight ``1 / (groups in its game)``
so every game counts once in the loss and in the primary metrics. A long
game cannot dominate by contributing more 30-second checkpoints.
Checkpoint-weighted metrics are also reported, labeled as such.

Optimization: the objective is the weighted mean negative log-likelihood
plus ``l2/2 * |w|^2``. It is strictly convex for ``l2 > 0``, so it has one
minimum. Newton's method with backtracking line search runs until the
gradient's max-norm is below ``tol``. A fit that does not reach that is
reported as ``not_converged`` and no metrics or model are written.

Splits: by tournament session, oldest sessions train, then validation (picks
``l2``), newest test. Preprocessing is fit on the training split only.
With too few sessions the fallback is leave-one-game-out, labeled
``game_held_out_weaker`` because games in one session share lobbies and
teams.

Below ``--min-games`` usable games no model is fit and no metrics exist.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from features import CHECKPOINT_STEP, FEATURES, build_dataset, load_accepted

MIN_GAMES = 20
MIN_GAMES_FLOOR = 5
# Session split needs at least this many sessions and games per split;
# otherwise the weaker game-held-out evaluation is used.
MIN_SESSIONS = 3
MIN_TEST_GAMES = 5
MIN_VALIDATION_GAMES = 3
# Calibration bins are reported only with this many evaluated games.
MIN_CALIBRATION_GAMES = 20
CALIBRATION_BINS = 10
L2_GRID = (0.01, 0.1, 1.0)
DEFAULT_L2 = 0.1
TOL = 1e-6
MAX_ITER = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- preprocessing ---------------------------------------------------------


CONDITION_FEATURES = (
    "players_alive",
    "players_downed",
    "players_out",
    "players_status_unknown",
    "no_standing",
    "hp_frac",
    "hp_frac_missing",
)
POSITION_FEATURES = tuple(name for name in FEATURES if name not in CONDITION_FEATURES)
FEATURE_SETS = {"condition_plus_position": FEATURES, "condition_only": CONDITION_FEATURES}


def fit_scaler(groups: list[dict], names=FEATURES) -> dict:
    """Mean and standard deviation of each known feature value in ``groups``."""
    scaler = {}
    for name in names:
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
    """Standardized features, in ``scaler`` order. Unknown becomes the training mean (0)."""
    return [
        0.0 if row["features"][name] is None
        else (row["features"][name] - scaler[name]["mean"]) / scaler[name]["std"]
        for name in scaler
    ]


def game_weights(groups: list[dict]) -> list[float]:
    counts: dict = {}
    for group in groups:
        counts[group["game_id"]] = counts.get(group["game_id"], 0) + 1
    return [1.0 / counts[group["game_id"]] for group in groups]


# --- conditional logit -----------------------------------------------------


def softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    exps = [math.exp(score - top) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def objective(beta: list[float], data: list, weights: list[float], l2: float, hessian: bool = False):
    """Loss, gradient, and optionally Hessian of the weighted penalized NLL."""
    dim = len(beta)
    total = sum(weights)
    loss = 0.5 * l2 * _dot(beta, beta)
    grad = [l2 * b for b in beta]
    hess = [[l2 if i == j else 0.0 for j in range(dim)] for i in range(dim)] if hessian else None
    for (vectors, label), weight in zip(data, weights):
        w = weight / total
        probs = softmax([_dot(beta, vector) for vector in vectors])
        loss -= w * math.log(max(probs[label], 1e-300))
        mean = [sum(p * vector[j] for p, vector in zip(probs, vectors)) for j in range(dim)]
        for j in range(dim):
            grad[j] += w * (mean[j] - vectors[label][j])
        if hessian:
            for p, vector in zip(probs, vectors):
                for i in range(dim):
                    for j in range(dim):
                        hess[i][j] += w * p * vector[i] * vector[j]
            for i in range(dim):
                for j in range(dim):
                    hess[i][j] -= w * mean[i] * mean[j]
    return loss, grad, hess


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def fit(
    groups: list[dict],
    l2: float = DEFAULT_L2,
    tol: float = TOL,
    max_iter: int = MAX_ITER,
    features=FEATURES,
) -> dict:
    """Newton's method with Armijo backtracking. Scaler is fit on ``groups`` only."""
    if l2 <= 0:
        raise ValueError("l2 must be positive so the objective is strictly convex")
    scaler = fit_scaler(groups, features)
    data = [([vectorize(row, scaler) for row in group["rows"]], group["label"]) for group in groups]
    weights = game_weights(groups)
    beta = [0.0] * len(features)
    history = []
    converged = False
    for iteration in range(max_iter + 1):
        loss, grad, hess = objective(beta, data, weights, l2, hessian=True)
        grad_norm = max(abs(g) for g in grad)
        history.append({"iteration": iteration, "loss": loss, "grad_max": grad_norm})
        if grad_norm < tol:
            converged = True
            break
        if iteration == max_iter:
            break
        step = _solve(hess, [-g for g in grad])
        slope = _dot(grad, step)
        size = 1.0
        while size > 1e-10:
            trial = [b + size * s for b, s in zip(beta, step)]
            if objective(trial, data, weights, l2)[0] <= loss + 1e-4 * size * slope:
                break
            size *= 0.5
        beta = trial
    return {
        "features": list(features),
        "weights": beta,
        "scaler": scaler,
        "l2": l2,
        "optimizer": {
            "method": "newton_armijo",
            "converged": converged,
            "iterations": history[-1]["iteration"],
            "final_loss": history[-1]["loss"],
            "final_grad_max": history[-1]["grad_max"],
            "tol": tol,
            "loss_trace": [round(item["loss"], 10) for item in history],
        },
    }


def predict(model: dict, group: dict) -> list[float]:
    # ``vectorize`` follows the model's own scaler, so feature subsets work.
    vectors = [vectorize(row, model["scaler"]) for row in group["rows"]]
    return softmax([_dot(model["weights"], vector) for vector in vectors])


# --- baselines and metrics -------------------------------------------------


def uniform_baseline(group: dict) -> list[float]:
    """Equal probability for every squad in the lobby, eliminated or not."""
    return [1.0 / len(group["rows"])] * len(group["rows"])


def contending(group: dict) -> list[bool]:
    """Squads not verified eliminated. Unverified groups keep every squad."""
    if not group.get("elimination_verified"):
        return [True] * len(group["rows"])
    return [not row.get("eliminated") for row in group["rows"]]


def contention_baseline(group: dict) -> list[float]:
    """Equal probability over squads in contention; verified-eliminated squads get 0."""
    alive = contending(group)
    return [1.0 / sum(alive) if flag else 0.0 for flag in alive]


def eligibility(group: dict) -> str:
    """``verified``: eliminations at or before this checkpoint are confirmed and
    applied. ``unverified``: the evidence is inconsistent, so no squad is
    excluded and probabilities stay unconstrained."""
    return "verified" if group.get("elimination_verified") else "unverified"


def masked(probs: list[float], group: dict) -> list[float]:
    """Eligibility-constrained probabilities.

    Squads with a verified elimination at or before the checkpoint get exactly
    0; the rest are renormalized. Unverified groups are returned unchanged.
    """
    alive = contending(group)
    kept = [p if flag else 0.0 for p, flag in zip(probs, alive)]
    total = sum(kept)
    return [p / total for p in kept]


def players_baseline(group: dict) -> list[float]:
    """All probability on the squads with the most standing players, split evenly."""
    counts = [
        row["features"]["players_alive"] + row["features"]["players_downed"]
        for row in group["rows"]
    ]
    best = max(counts)
    tied = counts.count(best)
    return [1.0 / tied if count == best else 0.0 for count in counts]


METHODS = {
    "model": None,
    "model_masked_diagnostic": None,
    "uniform": uniform_baseline,
    "uniform_contention": contention_baseline,
    "most_players": players_baseline,
}


def score(probs: list[float], label: int) -> dict:
    """Log loss, multi-class Brier score, and top-1 credit split across ties."""
    top = max(probs)
    tied = [i for i, prob in enumerate(probs) if prob == top]
    return {
        "log_loss": -math.log(max(probs[label], 1e-15)),
        "brier": sum((p - (1.0 if i == label else 0.0)) ** 2 for i, p in enumerate(probs)),
        "top1": (1.0 / len(tied)) if label in tied else 0.0,
    }


def _mean(items: list[dict], weights: list[float], method: str, metric: str) -> float:
    return sum(w * item[method][metric] for item, w in zip(items, weights)) / sum(weights)


def summarize(scored: list[dict]) -> dict:
    """Game-weighted (primary) and checkpoint-weighted means."""
    if not scored:
        return {"groups": 0}
    per_game = game_weights(scored)
    flat = [1.0] * len(scored)
    out = {"groups": len(scored), "games": len({item["game_id"] for item in scored})}
    for label, weights in (("game_weighted", per_game), ("checkpoint_weighted", flat)):
        out[label] = {
            method: {metric: _mean(scored, weights, method, metric) for metric in ("log_loss", "brier", "top1")}
            for method in METHODS
        }
    return out


def calibration(scored: list[dict]) -> dict:
    """Game-weighted reliability bins over every squad's predicted probability."""
    games = len({item["game_id"] for item in scored})
    if games < MIN_CALIBRATION_GAMES:
        return {"status": "insufficient_data", "games": games, "required": MIN_CALIBRATION_GAMES}
    weights = game_weights(scored)
    bins = [{"weight": 0.0, "p": 0.0, "won": 0.0, "rows": 0, "games": set()} for _ in range(CALIBRATION_BINS)]
    for item, weight in zip(scored, weights):
        for index, prob in enumerate(item["probs"]):
            slot = bins[min(int(prob * CALIBRATION_BINS), CALIBRATION_BINS - 1)]
            slot["weight"] += weight
            slot["p"] += weight * prob
            slot["won"] += weight * (1.0 if index == item["label"] else 0.0)
            slot["rows"] += 1
            slot["games"].add(item["game_id"])
    total = sum(slot["weight"] for slot in bins)
    table = []
    ece = 0.0
    for index, slot in enumerate(bins):
        if not slot["weight"]:
            continue
        mean_p = slot["p"] / slot["weight"]
        observed = slot["won"] / slot["weight"]
        ece += slot["weight"] / total * abs(mean_p - observed)
        table.append(
            {
                "bin": [index / CALIBRATION_BINS, (index + 1) / CALIBRATION_BINS],
                "mean_predicted": mean_p,
                "observed_win_rate": observed,
                "squad_rows": slot["rows"],
                "games": len(slot["games"]),
            }
        )
    return {"status": "ok", "games": games, "ece_game_weighted": ece, "bins": table}


def by_elapsed(scored: list[dict], width: int = 300) -> dict:
    buckets: dict[int, list[dict]] = {}
    for item in scored:
        buckets.setdefault(item["elapsed"] // width, []).append(item)
    return {f"{key * width}-{(key + 1) * width}s": summarize(items) for key, items in sorted(buckets.items())}


def score_groups(model: dict, groups: list[dict]) -> list[dict]:
    scored = []
    for group in groups:
        probs = predict(model, group)
        item = {
            "game_id": group["game_id"],
            "session_id": group.get("session_id"),
            "t": group["t"],
            "elapsed": group["elapsed"],
            "squads": len(group["rows"]),
            "label": group["label"],
            "probs": probs,
            "model": score(probs, group["label"]),
            "model_masked_diagnostic": score(masked(probs, group), group["label"]),
        }
        for name, baseline in METHODS.items():
            if baseline:
                item[name] = score(baseline(group), group["label"])
        scored.append(item)
    return scored


def evaluation_report(scored: list[dict]) -> dict:
    return {
        "primary": "game_weighted log_loss; uniform is the reference baseline",
        "overall": summarize(scored),
        "by_elapsed": by_elapsed(scored),
        "calibration": calibration(scored),
    }


# --- splits ----------------------------------------------------------------


def session_split(groups: list[dict], session_order: list) -> dict | None:
    """Oldest sessions train, then validation, newest test. ``None`` if too thin."""
    sessions = [s for s in session_order if any(g["session_id"] == s for g in groups)]
    if len(sessions) < MIN_SESSIONS:
        return None
    n_test = max(1, round(0.2 * len(sessions)))
    n_val = max(1, round(0.2 * len(sessions)))
    if len(sessions) - n_test - n_val < 1:
        return None
    split = {
        "train": sessions[: len(sessions) - n_test - n_val],
        "validation": sessions[len(sessions) - n_test - n_val : len(sessions) - n_test],
        "test": sessions[len(sessions) - n_test :],
    }
    games = {
        name: {g["game_id"] for g in groups if g["session_id"] in members}
        for name, members in split.items()
    }
    if len(games["test"]) < MIN_TEST_GAMES or len(games["validation"]) < MIN_VALIDATION_GAMES:
        return None
    return split


def _non_converged(models: list[dict]) -> list[dict]:
    return [m["optimizer"] for m in models if not m["optimizer"]["converged"]]


def evaluate_by_session(groups: list[dict], split: dict, features=FEATURES) -> dict:
    part = {name: [g for g in groups if g["session_id"] in members] for name, members in split.items()}
    fits = []
    selection = []
    for l2 in L2_GRID:
        model = fit(part["train"], l2=l2, features=features)
        fits.append(model)
        val = summarize(score_groups(model, part["validation"]))
        selection.append({"l2": l2, "validation_log_loss": val["game_weighted"]["model"]["log_loss"]})
    chosen = min(selection, key=lambda item: item["validation_log_loss"])["l2"]
    final = fit(part["train"] + part["validation"], l2=chosen, features=features)
    fits.append(final)
    result = {
        "method": "session_split",
        "features": list(features),
        "sessions": split,
        "games": {name: len({g["game_id"] for g in items}) for name, items in part.items()},
        "l2_selection": selection,
        "l2": chosen,
        "preprocessing_fit_on": "train for selection; train+validation for the test model",
        "optimizer": final["optimizer"],
    }
    bad = _non_converged(fits)
    if bad:
        return {**result, "status": "not_converged", "non_converged": bad}
    result["test"] = evaluation_report(score_groups(final, part["test"]))
    result["status"] = "ok"
    return result


def leave_one_game_out(groups: list[dict], l2: float = DEFAULT_L2) -> dict:
    games = sorted({group["game_id"] for group in groups}, key=str)
    scored = []
    fits = []
    for held in games:
        model = fit([g for g in groups if g["game_id"] != held], l2=l2)
        fits.append(model)
        scored.extend(score_groups(model, [g for g in groups if g["game_id"] == held]))
    result = {
        "method": "game_held_out_weaker",
        "warning": (
            "Games are held out one at a time, but held-out games share sessions, "
            "lobbies, and teams with training games. This is a weaker test than a "
            "session split and likely optimistic. l2 is fixed, not tuned."
        ),
        "folds": len(games),
        "l2": l2,
        "preprocessing_fit_on": "each fold's training games",
        "optimizer_iterations": [m["optimizer"]["iterations"] for m in fits],
    }
    bad = _non_converged(fits)
    if bad:
        return {**result, "status": "not_converged", "non_converged": bad}
    result["test"] = evaluation_report(scored)
    result["status"] = "ok"
    return result


# --- candidate audit -------------------------------------------------------


def _distribution(values: list[int]) -> dict:
    counts: dict = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: int(kv[0])))


def candidate_summary(groups: list[dict]) -> dict:
    """What the uniform baselines divide by, and how status relates to elimination."""
    verified = [g for g in groups if g.get("elimination_verified")]
    cross = {
        f"{standing}_{state}": 0
        for standing in ("no_standing", "standing")
        for state in ("eliminated", "not_eliminated")
    }
    for group in verified:
        for row in group["rows"]:
            standing = "no_standing" if row["features"]["no_standing"] else "standing"
            state = "eliminated" if row.get("eliminated") else "not_eliminated"
            cross[f"{standing}_{state}"] += 1
    contending_counts = [sum(contending(g)) for g in groups]
    return {
        "groups": len(groups),
        "lobby_size": _distribution([len(g["rows"]) for g in groups]),
        "lobby_size_games": _distribution(
            [len(g["rows"]) for g in {g["game_id"]: g for g in groups}.values()]
        ),
        "elimination_verified_groups": len(verified),
        "elimination_unverified_groups": len(groups) - len(verified),
        "in_contention": _distribution(contending_counts),
        "mean_lobby_size": sum(len(g["rows"]) for g in groups) / len(groups),
        "mean_in_contention": sum(contending_counts) / len(groups),
        "in_contention_equals_teams_alive": sum(
            1 for g, n in zip(groups, contending_counts) if g.get("elimination_verified") and n == g.get("teams_alive")
        ),
        "status_vs_elimination_rows": cross,
    }


# --- driver ----------------------------------------------------------------


def _session_order(records: list[dict]) -> list:
    """Sessions oldest first by earliest ``match_start``; unknown times last."""
    first: dict = {}
    for record in records:
        start = record.get("match_start")
        start = float(start) if isinstance(start, (int, float)) and not isinstance(start, bool) else math.inf
        session = record.get("session_id")
        first[session] = min(first.get(session, math.inf), start)
    return sorted(first, key=lambda s: (first[s], str(s)))


def run(data_dir: Path, out_dir: Path, min_games: int, step: int = CHECKPOINT_STEP) -> dict:
    if min_games < MIN_GAMES_FLOOR:
        raise SystemExit(f"--min-games cannot be below {MIN_GAMES_FLOOR}")
    records, inventory = load_accepted(data_dir)
    report = {
        "generated_at": _now(),
        "data_dir": data_dir.as_posix(),
        "min_games": min_games,
        "checkpoint_step_s": step,
        "inventory": inventory,
        "evaluation": None,
        "model_path": None,
    }
    if len(records) < min_games:
        report["status"] = "insufficient_data"
        report["detail"] = f"{len(records)} usable accepted games; {min_games} required"
        return report

    groups, dropped, diagnostics = build_dataset(records, data_dir, step)
    games = sorted({group["game_id"] for group in groups}, key=str)
    report["dataset"] = {
        "games_with_groups": len(games),
        "checkpoint_groups": len(groups),
        "squad_rows": sum(len(group["rows"]) for group in groups),
        "dropped": dropped,
        "eligibility": diagnostics,
        "sessions": sorted({str(group["session_id"]) for group in groups}),
    }
    if len(games) < min_games:
        report["status"] = "insufficient_data"
        report["detail"] = f"{len(games)} games produced labeled groups; {min_games} required"
        return report

    report["candidates"] = candidate_summary(groups)
    split = session_split(groups, _session_order(records))
    evaluation = evaluate_by_session(groups, split) if split else leave_one_game_out(groups)
    report["evaluation"] = evaluation
    if evaluation["status"] != "ok":
        report["status"] = evaluation["status"]
        return report
    if split:
        # Same split and validation-based l2 selection; test is scored once.
        ablation = evaluate_by_session(groups, split, features=CONDITION_FEATURES)
        report["ablation"] = {
            "question": "do position features add value beyond squad condition?",
            "condition_only": ablation,
            "condition_features": list(CONDITION_FEATURES),
            "position_features": list(POSITION_FEATURES),
        }

    final = fit(groups, l2=evaluation["l2"])
    if not final["optimizer"]["converged"]:
        report["status"] = "not_converged"
        report["detail"] = "final fit on all games"
        return report
    final["trained_on_games"] = games
    final["checkpoint_step_s"] = step
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.json"
    model_path.write_text(json.dumps(final, indent=2) + "\n", encoding="utf-8")
    report["model_path"] = model_path.as_posix()
    report["status"] = "trained"
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--min-games", type=int, default=MIN_GAMES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run(args.data_dir, args.out_dir, args.min_games)
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
    evaluation = report.get("evaluation") or {}
    if evaluation.get("status") == "ok":
        print(f"evaluation: {evaluation['method']}")
        overall = evaluation["test"]["overall"]["game_weighted"]
        for method in METHODS:
            values = overall[method]
            print(
                f"  {method:12s} log_loss {values['log_loss']:.3f} "
                f"brier {values['brier']:.3f} top1 {values['top1']:.3f}"
            )
    print(f"report: {report_path.as_posix()}")
    return 0 if report["status"] == "trained" else 2


if __name__ == "__main__":
    sys.exit(main())
