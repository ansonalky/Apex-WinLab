"""Forward pass of the saved model for games it was not trained on.

Uses ``data/models/model.json`` and that file's scaler exactly as stored.
Nothing here fits, retunes, or writes the model, the frozen split, the
held-out bundle, or the evaluation report.

Original games keep their current ``held_out`` and ``final_model_fitted``
rows. This module is the shared path for expansion games, URL imports that
were saved, and temporary previews.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import features as features_module
from features import FEATURES, contention, replay_checkpoints, squad_rows
from train_model import contention_baseline, masked, predict

NEW_MATCH = "new_match"
LABEL = "New-match prediction · model trained on the original 51 games."
UNVERIFIED = "Training membership cannot be verified for this replay."
PREVIEW_LIMITATION = "Performance on low-quality preview matches has not been established."
SAVE_QUALITY = Decimal("7.0")
MODEL_FILE = "models/model.json"


def feature_code_version() -> str:
    """Hash of the feature-extraction module. A code change misses the old cache."""
    path = Path(features_module.__file__ or "")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric_quality(value: Any) -> Decimal | None:
    """The stored quality number. Display text and non-numbers are not scores."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite():
        return None
    return number


def save_quality(value: Any) -> bool:
    """True when the stored score is at least 7.0. Rounding is not used."""
    score = numeric_quality(value)
    return score is not None and score >= SAVE_QUALITY


def model_path(data_dir: Path) -> Path:
    return Path(data_dir) / "models" / "model.json"


def load_model(data_dir: Path) -> dict | None:
    """The saved model and its file hash, or None when the file cannot be used."""
    path = model_path(data_dir)
    if not path.is_file():
        return None
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(model, dict):
        return None
    return {"model": model, "sha256": _sha256(path), "path": path}


def incompatible_reason(model: dict | None) -> str | None:
    """Why this file cannot score a row. None means the saved preprocessing applies."""
    if not isinstance(model, dict):
        return f"{MODEL_FILE} is not in the data directory or could not be read."
    features = model.get("features")
    scaler = model.get("scaler")
    weights = model.get("weights")
    if list(features or []) != list(FEATURES):
        return (
            f"{MODEL_FILE} features do not match the current feature code, so no prediction was made."
        )
    if not isinstance(scaler, dict) or not isinstance(weights, list) or len(weights) != len(FEATURES):
        return f"{MODEL_FILE} is missing its saved scaler or weights, so no prediction was made."
    for name in FEATURES:
        stats = scaler.get(name)
        if not isinstance(stats, dict) or "mean" not in stats or "std" not in stats:
            return f"{MODEL_FILE} has no saved scaler for {name}, so no prediction was made."
        try:
            std = float(stats["std"])
        except (TypeError, ValueError):
            return f"{MODEL_FILE} has a non-numeric scaler for {name}, so no prediction was made."
        if not math.isfinite(std) or std == 0:
            return f"{MODEL_FILE} has a zero scaler for {name}, so no prediction was made."
    return None


def training_inventory(data_dir: Path) -> dict:
    """Game ids from the model file and replay hashes from the original manifest.

    The manifest is only read. A hash that is not stored there is left unknown
    rather than treated as unseen.
    """
    loaded = load_model(data_dir)
    model = (loaded or {}).get("model") or {}
    ids = []
    for game_id in model.get("trained_on_games") or []:
        if isinstance(game_id, bool):
            continue
        try:
            ids.append(int(game_id))
        except (TypeError, ValueError):
            continue
    hashes: dict[int, str] = {}
    manifest = Path(data_dir) / "collection" / "manifest.json"
    document = None
    if manifest.is_file():
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = None
    if isinstance(document, dict):
        for record in (document.get("games") or {}).values():
            if not isinstance(record, dict):
                continue
            try:
                game_id = int(record.get("game_id"))
            except (TypeError, ValueError):
                continue
            digest = record.get("sha256")
            if game_id in ids and isinstance(digest, str) and digest:
                hashes[game_id] = digest
    by_hash: dict[str, int] = {}
    for game_id, digest in hashes.items():
        by_hash.setdefault(digest, game_id)
    return {
        "game_ids": set(ids),
        "hashes": hashes,
        "by_hash": by_hash,
        "model_sha256": (loaded or {}).get("sha256"),
        "complete": bool(ids) and len(hashes) == len(set(ids)),
    }


def classify(game_id: Any, telemetry_sha256: str | None, inventory: dict) -> dict:
    """Whether this replay is one of the training games.

    ``verified_unseen`` requires both the game id and the replay hash to be
    absent from the training inventory. Anything short of that is unverified.
    """
    try:
        game_id = int(game_id)
    except (TypeError, ValueError):
        return {"membership": "unverified", "reason": UNVERIFIED + " The game id is not a number."}
    ids = inventory.get("game_ids") or set()
    hashes = inventory.get("hashes") or {}
    by_hash = inventory.get("by_hash") or {}
    if not ids or not inventory.get("complete"):
        return {
            "membership": "unverified",
            "reason": UNVERIFIED + " The training inventory does not list a replay hash for every trained game.",
        }
    if not telemetry_sha256:
        return {"membership": "unverified", "reason": UNVERIFIED + " The replay file has no hash."}
    trained_hash = hashes.get(game_id)
    if game_id in ids:
        if trained_hash == telemetry_sha256:
            return {"membership": "original", "reason": "This replay is one of the original 51 training games."}
        return {
            "membership": "unverified",
            "reason": UNVERIFIED + " This game id is in the training list, but the replay hash does not match.",
        }
    owner = by_hash.get(telemetry_sha256)
    if owner is not None:
        return {
            "membership": "unverified",
            "reason": UNVERIFIED + f" This replay hash matches training game {owner}.",
        }
    return {"membership": "verified_unseen", "reason": LABEL}


def build_table(series: list, model: dict) -> dict:
    """Per-second probabilities from the saved model.

    The same extraction and masking the live path already uses. The winner is
    not an input. Seconds at and after the cut-off are omitted, so the
    recorded result is not turned into a probability.
    """
    reason = incompatible_reason(model)
    if reason:
        return {"ok": False, "reason": reason, "table": {}, "cutoff_t": None, "playing_start": None}
    if not isinstance(series, list) or not series:
        return {
            "ok": False,
            "reason": "Replay telemetry has no series, so no prediction was made.",
            "table": {},
            "cutoff_t": None,
            "playing_start": None,
        }
    replayed = replay_checkpoints(series, 1)
    if replayed["failures"]:
        return {
            "ok": False,
            "reason": "Replay reconstruction failed, so no prediction was made.",
            "table": {},
            "cutoff_t": replayed["cutoff"],
            "playing_start": replayed["playing_start"],
        }
    table: dict[int, dict] = {}
    for t in sorted(replayed["snapshots"]):
        rows = squad_rows(replayed["snapshots"][t])
        if not rows:
            continue
        keys = [row["team"] for row in rows]
        meta = replayed["meta"][t]
        status = contention(meta, keys, None)
        for row, flag in zip(rows, status["flags"]):
            row["eliminated"] = flag
        group = {"rows": rows, "elimination_verified": status["verified"]}
        raw = predict(model, group)
        constrained = masked(raw, group)
        baseline = contention_baseline(group)
        table[t] = {
            "t": t,
            "elapsed": meta["elapsed"],
            "eligibility": "verified" if status["verified"] else "unverified",
            "eligibility_reasons": status["reasons"],
            "elimination_verified": status["verified"],
            "in_contention": sum(1 for row in rows if not row["eliminated"]) if status["verified"] else len(rows),
            "squads": [
                {
                    "teamId": row["team"],
                    "team_name": row.get("team_name"),
                    "eliminated": row["eliminated"],
                    "model": c,
                    "model_unconstrained": r,
                    "uniform_contention": b,
                    "features": row["features"],
                }
                for row, r, c, b in zip(rows, raw, constrained, baseline)
            ],
        }
    if not table:
        return {
            "ok": False,
            "reason": "The replay has no Playing seconds before the prediction cut-off.",
            "table": {},
            "cutoff_t": replayed["cutoff"],
            "playing_start": replayed["playing_start"],
        }
    return {
        "ok": True,
        "reason": None,
        "table": table,
        "cutoff_t": replayed["cutoff"],
        "playing_start": replayed["playing_start"],
        "replay_match_start": (replayed.get("state") or {}).get("match_start"),
    }


def cache_key(telemetry_sha256: str | None, model_sha256: str | None, code_version: str | None) -> str:
    return "|".join([telemetry_sha256 or "", model_sha256 or "", code_version or ""])


def provenance_for(*, model_sha256: str, telemetry_sha256: str | None, membership: str, code_version: str) -> dict:
    verified = membership == "verified_unseen"
    return {
        "kind": NEW_MATCH,
        "label": LABEL if verified else UNVERIFIED,
        "file": MODEL_FILE,
        "sha256": model_sha256,
        "telemetry_sha256": telemetry_sha256,
        "feature_code_version": code_version,
        "cache_key": cache_key(telemetry_sha256, model_sha256, code_version),
        "fit_on": "the original 51 games",
        "held_out": False,
        "in_sample_game": False,
        "membership": membership,
        "sampling": "every replay second from Playing start to the cut-off",
        "features_from": "features.replay_checkpoints(step=1) + squad_rows on the local replay",
        "missing_values": "an unknown feature becomes the saved training mean (standardized 0); the row's missingness flags are left as extracted",
        "scaler": "the model file's own scaler",
    }


def describe(data_dir: Path, game_id: Any, quality: Any = None) -> dict:
    """What the dashboard should say, without computing the per-second table."""
    from dashboard_data import _raw_path, _record

    data_dir = Path(data_dir)
    record = _record(data_dir, game_id)
    path = _raw_path(record, data_dir) if record else None
    digest = _sha256(path) if path is not None else None
    inventory = training_inventory(data_dir)
    loaded = load_model(data_dir)
    model = (loaded or {}).get("model")
    problem = incompatible_reason(model)
    stored_quality = quality if quality is not None or record is None else record.get("quality_score")
    if record is not None and quality is None:
        stored_quality = record.get("quality_score")
    preview_record = bool(record and record.get("_preview"))
    accepted = bool(record) and record.get("acceptance_status") == "accepted" and not preview_record
    storage = "preview" if preview_record or not (accepted and save_quality(stored_quality)) else "saved"
    if record is None or path is None:
        return {
            "role": "unavailable",
            "predict": False,
            "storage": None,
            "membership": "unverified",
            "reason": "Replay telemetry for this match is not on disk, so no prediction was made.",
            "limitation": None,
            "provenance": None,
        }
    classified = classify(game_id, digest, inventory)
    if classified["membership"] == "original":
        return {
            "role": "original",
            "predict": False,
            "storage": "saved",
            "membership": "original",
            "reason": classified["reason"],
            "limitation": None,
            "telemetry_sha256": digest,
            "model_sha256": (loaded or {}).get("sha256"),
            "feature_code_version": feature_code_version(),
            "provenance": None,
        }
    if problem or loaded is None:
        return {
            "role": "unavailable",
            "predict": False,
            "storage": storage,
            "membership": classified["membership"],
            "reason": problem or f"{MODEL_FILE} is not in the data directory.",
            "limitation": PREVIEW_LIMITATION if storage == "preview" else None,
            "telemetry_sha256": digest,
            "model_sha256": (loaded or {}).get("sha256"),
            "feature_code_version": feature_code_version(),
            "provenance": None,
        }
    code_version = feature_code_version()
    limitation = PREVIEW_LIMITATION if storage == "preview" else None
    if classified["membership"] != "verified_unseen":
        limitation = classified["reason"] if not limitation else f"{classified['reason']} {limitation}"
    return {
        "role": "new_match",
        "predict": True,
        "storage": storage,
        "membership": classified["membership"],
        "reason": classified["reason"],
        "label": LABEL if classified["membership"] == "verified_unseen" else UNVERIFIED,
        "limitation": limitation,
        "telemetry_sha256": digest,
        "model_sha256": loaded["sha256"],
        "feature_code_version": code_version,
        "provenance": provenance_for(
            model_sha256=loaded["sha256"],
            telemetry_sha256=digest,
            membership=classified["membership"],
            code_version=code_version,
        ),
    }


def ensure_kind(connection) -> None:
    """Allow ``new_match`` rows beside the two frozen kinds. Existing rows stay."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'squad_estimates_kind'"
        )
        row = cursor.fetchone()
        if row and "new_match" in str(row[0]):
            return
        cursor.execute("ALTER TABLE squad_estimates DROP CONSTRAINT IF EXISTS squad_estimates_kind")
        cursor.execute(
            """
            ALTER TABLE squad_estimates ADD CONSTRAINT squad_estimates_kind
            CHECK (prediction_kind IN ('held_out', 'final_model_fitted', 'new_match'))
            """
        )


def persist_new_match(connection, game_id: int, table: dict[int, dict], *, provenance: dict, replay_match_start) -> dict:
    """Replace only this game's ``new_match`` rows, in one transaction.

    Refuses original games and anything that is not a saved match. Held-out
    and fitted rows are not deleted.
    """
    from psycopg.pq import TransactionStatus

    from tiger_rows import _estimate_rows

    if provenance.get("membership") == "original" or provenance.get("kind") != NEW_MATCH:
        raise RuntimeError("original games keep their existing prediction rows")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("persist_new_match needs an idle connection")
    points = []
    for point in table.values():
        points.append(
            {
                **point,
                "squads": [
                    {
                        **squad,
                        "in_contention": (not squad["eliminated"]) if point["elimination_verified"] else True,
                    }
                    for squad in point["squads"]
                ],
            }
        )
    version = json.dumps(provenance, sort_keys=True)
    rows = _estimate_rows(int(game_id), NEW_MATCH, version, replay_match_start, points)
    insert = """
        INSERT INTO squad_estimates (
          game_id, t, elapsed_s, ts, team_id, prediction_kind, model_version,
          model_probability, model_probability_raw, baseline_name, baseline_probability, in_contention,
          eliminated, elimination_verified
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with connection.transaction():
        ensure_kind(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM squad_estimates WHERE game_id = %s AND prediction_kind = %s",
                (int(game_id), NEW_MATCH),
            )
            if rows:
                cursor.executemany(insert, rows)
    return {"game_id": int(game_id), "rows": len(rows), "prediction_kind": NEW_MATCH}
