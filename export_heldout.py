#!/usr/bin/env python3
"""Freeze the session-split evaluation and export its held-out predictions.

``freeze`` records the split, ``l2``, features, data hashes, and test metrics
from a finished ``report.json`` into ``config/frozen_evaluation.json``. It
refuses to overwrite an existing freeze.

``export`` reads only the frozen config. It does not select ``l2``,
features, or sessions. It refits the evaluation model on train + validation
with the frozen settings, scores the test sessions, checks that every test
metric equals the frozen value, and only then writes a bundle:

    <out>/<name>/bundle.json               metadata, split, games, file hashes
    <out>/<name>/heldout_predictions.json  one entry per test checkpoint
    <out>/<name>/metrics.json              overall, by elapsed, per game, calibration
    <out>/<name>/evaluation_model.json     train + validation fit
    <out>/<name>/README.md
    <out>/<name>.tar.gz (+ .sha256)

These are held-out evaluation predictions. Final-model predictions
(``data/models/model.json``, fit on every game) are a different artifact
and are never written here.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import train_model
from features import CHECKPOINT_STEP, build_dataset, load_accepted

SCHEMA_VERSION = 1
CONFIG = Path("config/frozen_evaluation.json")
PREDICTION_TYPE = "held_out_session_split_test"
METRIC_TOLERANCE = 1e-9
POLICY = (
    "Frozen after the evaluation audit. Do not re-select l2, features, sessions, "
    "or baselines against these test games. A new evaluation needs new test sessions."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def git_state(paths: tuple[str, ...] = ("features.py", "train_model.py", "export_heldout.py")) -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    dirty = run("status", "--porcelain", "--", *paths)
    return {"commit": run("rev-parse", "HEAD"), "model_code_dirty": bool(dirty) if dirty is not None else None}


# --- freeze ----------------------------------------------------------------


def freeze(report: dict, records: list[dict]) -> dict:
    """Frozen settings from an ok session-split report."""
    evaluation = report.get("evaluation") or {}
    if evaluation.get("method") != "session_split" or evaluation.get("status") != "ok":
        raise SystemExit("freeze needs an ok session_split evaluation in report.json")
    by_session: dict = {}
    for record in records:
        by_session.setdefault(record.get("session_id"), []).append(record.get("game_id"))
    games = {
        name: sorted(game for session in sessions for game in by_session.get(session, []))
        for name, sessions in evaluation["sessions"].items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "frozen_at": _now(),
        "policy": POLICY,
        "source_report_generated_at": report.get("generated_at"),
        "method": "session_split",
        "sessions": evaluation["sessions"],
        "games": games,
        "l2": evaluation["l2"],
        "l2_selection": evaluation["l2_selection"],
        "features": evaluation.get("features") or list(train_model.FEATURES),
        "checkpoint_step_s": report.get("checkpoint_step_s", CHECKPOINT_STEP),
        "optimizer": {"method": "newton_armijo", "tol": train_model.TOL, "max_iter": train_model.MAX_ITER},
        "data_sha256": {str(r.get("game_id")): r.get("sha256") for r in sorted(records, key=lambda r: str(r.get("game_id")))},
        "test_metrics": evaluation["test"]["overall"],
    }


# --- export ----------------------------------------------------------------


def _check_data(config: dict, records: list[dict]) -> list[dict]:
    """Records for the frozen games, with matching hashes. Refuses otherwise."""
    by_id = {str(r.get("game_id")): r for r in records}
    problems = []
    for game_id, digest in config["data_sha256"].items():
        record = by_id.get(game_id)
        if record is None:
            problems.append(f"game {game_id} missing")
        elif record.get("sha256") != digest:
            problems.append(f"game {game_id} sha256 changed")
        else:
            raw = Path(record["raw_path"]).read_bytes()
            if _sha256(raw) != digest:
                problems.append(f"game {game_id} raw file does not match sha256")
    if problems:
        raise SystemExit("data does not match the frozen evaluation: " + "; ".join(problems))
    return [by_id[game_id] for game_id in config["data_sha256"]]


def _metrics_match(expected: dict, actual: dict) -> list[str]:
    diffs = []
    for weighting in ("game_weighted", "checkpoint_weighted"):
        for method, values in expected[weighting].items():
            for metric, value in values.items():
                got = actual[weighting][method][metric]
                if abs(got - value) > METRIC_TOLERANCE:
                    diffs.append(f"{weighting}.{method}.{metric}: frozen {value} vs {got}")
    return diffs


def _checkpoint_entry(group: dict, item: dict, model_probs: list[float], game: dict) -> dict:
    baselines = {
        "model": model_probs,
        "model_masked_diagnostic": train_model.masked(model_probs, group),
        "uniform": train_model.uniform_baseline(group),
        "uniform_contention": train_model.contention_baseline(group),
        "most_players": train_model.players_baseline(group),
    }
    contending = train_model.contending(group)
    squads = []
    for index, row in enumerate(group["rows"]):
        features = row["features"]
        squads.append(
            {
                "team_id": row["team"],
                "team_name": row.get("team_name"),
                "is_winner": index == group["label"],
                "eliminated": bool(row.get("eliminated")),
                "in_contention": contending[index],
                "standing_players": int(features["players_alive"] + features["players_downed"]),
                "probabilities": {name: probs[index] for name, probs in baselines.items()},
            }
        )
    start = game["replay_match_start"]
    unix = int(start) + group["t"] if start is not None else None
    return {
        "game_id": group["game_id"],
        "session_id": group["session_id"],
        "split": "test",
        "t": group["t"],
        "elapsed_s": group["elapsed"],
        "replay_time_unix": unix,
        "replay_time_utc": (
            datetime.fromtimestamp(unix, timezone.utc).isoformat().replace("+00:00", "Z") if unix is not None else None
        ),
        "teams_alive": group.get("teams_alive"),
        "lobby_size": len(group["rows"]),
        "in_contention": sum(contending),
        "elimination_verified": bool(group.get("elimination_verified")),
        "winner_team_id": group["rows"][group["label"]]["team"],
        "game_weight": None,
        "scores": {name: item[name] for name in train_model.METHODS},
        "squads": squads,
    }


def build_export(config: dict, data_dir: Path) -> dict:
    """Everything the bundle holds, in memory. Raises if metrics do not reproduce."""
    records, _inventory = load_accepted(data_dir)
    records = _check_data(config, records)
    groups, dropped, diagnostics = build_dataset(records, data_dir, config["checkpoint_step_s"])
    if dropped:
        raise SystemExit(f"extraction dropped games that the frozen evaluation used: {dropped}")
    sessions = config["sessions"]
    part = {name: [g for g in groups if g["session_id"] in members] for name, members in sessions.items()}
    for name, items in part.items():
        if sorted({g["game_id"] for g in items}) != config["games"][name]:
            raise SystemExit(f"{name} games differ from the frozen split")

    features = tuple(config["features"])
    model = train_model.fit(
        part["train"] + part["validation"],
        l2=config["l2"],
        tol=config["optimizer"]["tol"],
        max_iter=config["optimizer"]["max_iter"],
        features=features,
    )
    if not model["optimizer"]["converged"]:
        raise SystemExit("frozen evaluation model did not converge")
    model["trained_on_games"] = sorted({g["game_id"] for g in part["train"] + part["validation"]})
    model["trained_on_sessions"] = sessions["train"] + sessions["validation"]
    model["role"] = "evaluation_model_train_plus_validation"

    scored = train_model.score_groups(model, part["test"])
    metrics = train_model.evaluation_report(scored)
    diffs = _metrics_match(config["test_metrics"], metrics["overall"])
    if diffs:
        raise SystemExit("test metrics do not reproduce the frozen evaluation: " + "; ".join(diffs[:5]))

    by_record = {r.get("game_id"): r for r in records}
    games = {}
    for game_id in config["games"]["test"]:
        record = by_record[game_id]
        diag = diagnostics[str(game_id)]
        winner = record["validation"]["winner"]
        games[game_id] = {
            "game_id": game_id,
            "session_id": record.get("session_id"),
            "event": record.get("event"),
            "map_name": record.get("map_name"),
            "organizer_username": record.get("organizer_username"),
            "manifest_match_start": record.get("match_start"),
            "replay_match_start": diag["replay_match_start"],
            "playing_start_t": diag["playing_start"],
            "cutoff_t": diag["cutoff"],
            "checkpoints": diag["checkpoints"],
            "winner_team_id": winner.get("teamId"),
            "winner_team_name": winner.get("teamName"),
            "winner_label_source": winner.get("label_source"),
            "raw_sha256": record.get("sha256"),
        }

    weights = train_model.game_weights(part["test"])
    checkpoints = []
    for group, item, weight in zip(part["test"], scored, weights):
        entry = _checkpoint_entry(group, item, item["probs"], games[group["game_id"]])
        entry["game_weight"] = weight
        checkpoints.append(entry)
    checkpoints.sort(key=lambda e: (str(e["game_id"]), e["t"]))

    per_game = {
        str(game_id): train_model.summarize([s for s in scored if s["game_id"] == game_id])
        for game_id in config["games"]["test"]
    }
    return {
        "model": model,
        "checkpoints": checkpoints,
        "games": games,
        "metrics": {**metrics, "per_game": per_game},
    }


README = """# Held-out evaluation bundle

`prediction_type: {prediction_type}`. These are out-of-sample predictions for the {n_games} test games
({n_sessions} test sessions) of the frozen session split. The model that made them was fit on the
train and validation sessions only (`evaluation_model.json`). They are not final-model predictions.

Each entry in `heldout_predictions.json` is one 30-second checkpoint:

- `t`: seconds on the replay clock. `elapsed_s`: seconds since Playing start.
- `replay_time_unix` / `replay_time_utc`: the replay's own `match_start` + `t`. That clock differs from
  the games API `match_start` by 7-16 s; both values are listed per game in `bundle.json`.
- `squads[].probabilities`:
  - `model`: the held-out prediction.
  - `uniform`: 1 / lobby size.
  - `uniform_contention`: 1 / squads not verified eliminated; verified-eliminated squads get 0. This is
    the meaningful uniform reference.
  - `most_players`: all mass on the squads with the most standing players.
  - `model_masked_diagnostic`: the model with eliminated squads set to 0 and renormalized. Diagnostic only.
- `eliminated`: a squad elimination feed event at or before `t`, verified against `teamsAlive`. Not a model input.
- `scores`: per-checkpoint log loss, Brier (sum over squads of (p - y)^2, range 0-2), and top-1.
- `game_weight` = 1 / checkpoints in that game. Weighted means give each game equal weight.

`metrics.json` repeats the frozen test metrics, recomputed from these predictions. `export_heldout.py`
refuses to write the bundle unless they match the frozen values within {tolerance}.

Calibration is withheld below 20 evaluated games (project policy). Six test games is a small sample.
"""


def write_bundle(export: dict, config: dict, out_dir: Path, name: str, data_dir: Path) -> dict:
    target = out_dir / name
    if target.exists() or (out_dir / f"{name}.tar.gz").exists():
        raise SystemExit(f"{target} already exists; bundles are not overwritten")
    files = {
        "heldout_predictions.json": _dump(export["checkpoints"]),
        "metrics.json": _dump(export["metrics"]),
        "evaluation_model.json": _dump(export["model"]),
        "README.md": README.format(
            prediction_type=PREDICTION_TYPE,
            n_games=len(config["games"]["test"]),
            n_sessions=len(config["sessions"]["test"]),
            tolerance=METRIC_TOLERANCE,
        ).encode("utf-8"),
    }
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "bundle": name,
        "prediction_type": PREDICTION_TYPE,
        "held_out": True,
        "final_model_predictions_included": False,
        "generated_at": _now(),
        "code": git_state(),
        "data_dir": data_dir.as_posix(),
        "frozen_config": config,
        "evaluation_model": {
            "fit_on": "train + validation sessions",
            "l2": export["model"]["l2"],
            "features": export["model"]["features"],
            "optimizer": {
                key: export["model"]["optimizer"][key]
                for key in ("method", "converged", "iterations", "final_grad_max", "tol")
            },
        },
        "games": [export["games"][game_id] for game_id in config["games"]["test"]],
        "checkpoint_count": len(export["checkpoints"]),
        "methods": list(train_model.METHODS),
        "primary_metric": "game-weighted log loss; compare model with uniform_contention",
        "files": {path: {"sha256": _sha256(body), "bytes": len(body)} for path, body in files.items()},
    }
    files["bundle.json"] = _dump(bundle)

    target.mkdir(parents=True)
    for path, body in files.items():
        (target / path).write_bytes(body)
    archive = out_dir / f"{name}.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(files):
            info = tarfile.TarInfo(f"{name}/{path}")
            info.size = len(files[path])
            info.mtime = 0
            tar.addfile(info, io.BytesIO(files[path]))
    archive.write_bytes(buffer.getvalue())
    digest = _sha256(buffer.getvalue())
    (out_dir / f"{name}.tar.gz.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return {"directory": target.as_posix(), "archive": archive.as_posix(), "archive_sha256": digest}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    frz = sub.add_parser("freeze", help="Write config/frozen_evaluation.json from a report")
    frz.add_argument("--data-dir", type=Path, default=Path("data"))
    frz.add_argument("--report", type=Path, default=Path("data/models/report.json"))
    frz.add_argument("--config", type=Path, default=CONFIG)
    exp = sub.add_parser("export", help="Write the held-out bundle from the frozen config")
    exp.add_argument("--data-dir", type=Path, default=Path("data"))
    exp.add_argument("--config", type=Path, default=CONFIG)
    exp.add_argument("--out-dir", type=Path, default=Path("data/exports"))
    exp.add_argument("--name", default="heldout-session-split-v1")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "freeze":
        if args.config.exists():
            raise SystemExit(f"{args.config} exists; the evaluation is already frozen")
        records, _ = load_accepted(args.data_dir)
        config = freeze(json.loads(args.report.read_text(encoding="utf-8")), records)
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_bytes(_dump(config))
        print(f"frozen: {args.config.as_posix()}")
        return 0
    config = json.loads(args.config.read_text(encoding="utf-8"))
    export = build_export(config, args.data_dir)
    written = write_bundle(export, config, args.out_dir, args.name, args.data_dir)
    overall = export["metrics"]["overall"]["game_weighted"]
    print(f"test games {len(config['games']['test'])}, checkpoints {len(export['checkpoints'])}; metrics match frozen values")
    for method in ("model", "uniform_contention", "uniform"):
        print(f"  {method:20s} log_loss {overall[method]['log_loss']:.3f}")
    print(f"bundle: {written['directory']}")
    print(f"archive: {written['archive']}  sha256 {written['archive_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
