#!/usr/bin/env python3
"""Apex WinLab match dashboard.

Launch from the repository root, pointing at the telemetry directory:

    .venv/bin/python app.py --data-dir data --port 5050 --backend auto

``--backend files`` reads local files. ``--backend tiger`` reads Tiger Data
through ``TigerReader`` and fails if the database cannot be used.
``--backend auto`` uses Tiger Data when ``DATABASE_URL`` connects and holds
imported matches, else local files. The active backend, and why, is served at
``/api/backend`` and shown on the page.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template

from dashboard_data import MatchStore
from live_predictions import FINAL, HELD_OUT, LivePredictions
from map_api import map_bp, ring_at
from stats_api import stats_bp


def select_store(data_dir: Path, backend: str, dsn: str | None = None) -> tuple[object, dict]:
    """The store to serve, and a description of it for the page."""
    data_dir = data_dir.resolve()
    if backend == "files":
        return MatchStore(data_dir), {"backend": "files", "label": "Local files", "detail": f"{data_dir} (requested)"}
    from import_tiger import redact

    dsn = dsn if dsn is not None else os.environ.get("DATABASE_URL")
    reason = None
    if not dsn:
        reason = "DATABASE_URL is not set"
    else:
        try:
            from tiger_read import TigerReader
            from tiger_store import TigerStore

            reader = TigerReader(dsn)
            status = reader.ping()
            if status["matches"]:
                return TigerStore(reader), {
                    "backend": "tiger",
                    "label": "Tiger Data database",
                    "detail": f"SELECT 1 ok; {status['matches']} imported matches",
                }
            reason = "database reachable but holds no imported matches"
        except Exception as exc:  # any connection or query failure
            lines = str(exc).splitlines() or [""]
            reason = f"{type(exc).__name__}: {redact(lines[-1], dsn)}"
    if backend == "tiger":
        raise SystemExit(f"--backend tiger: database not usable ({reason})")
    return MatchStore(data_dir), {
        "backend": "files",
        "label": "Local files",
        "detail": f"{data_dir}; database not used: {reason}",
    }


def create_app(data_dir: Path, backend: str = "files", store=None, info: dict | None = None) -> Flask:
    app = Flask(__name__)
    if store is None:
        store, info = select_store(data_dir, backend)
    info = info or {"backend": getattr(store, "backend", "files"), "label": "", "detail": ""}
    app.config["DATA_BACKEND"] = info
    # The map and ring endpoints read local replay files under either backend.
    app.config["DATA_DIR"] = data_dir.resolve()
    app.register_blueprint(map_bp)
    app.register_blueprint(stats_bp)
    # Per-second predictions are computed from the local replay and the frozen
    # model files under either backend, and cached in memory.
    live = LivePredictions(data_dir.resolve())

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/backend")
    def data_backend():
        return jsonify(info)

    @app.get("/api/matches")
    def matches():
        return jsonify(store.matches())

    @app.get("/api/matches/<int:game_id>")
    def match_summary(game_id: int):
        summary = store.summary(game_id)
        if summary is None:
            abort(404)
        # Build this match's per-second predictions while the page loads.
        threading.Thread(
            target=lambda: [live.table(game_id, kind) for kind in (HELD_OUT, FINAL) if live.available(game_id, kind)],
            daemon=True,
        ).start()
        summary["live"] = {
            "heldout": live.model(HELD_OUT)["provenance"] if live.available(game_id, HELD_OUT) else None,
            "final": live.model(FINAL)["provenance"] if live.available(game_id, FINAL) else None,
        }
        return jsonify(summary)

    @app.get("/api/matches/<int:game_id>/frames/<int:index>")
    def match_frame(game_id: int, index: int):
        frame = store.frame(game_id, index)
        if frame is None:
            abort(404)
        return jsonify(frame)

    @app.get("/api/matches/<int:game_id>/at/<int:t>")
    def match_frame_at(game_id: int, t: int):
        """Everything shown for one second: frame, ring, and the predictions
        made at that same second, so the page never mixes timestamps."""
        started = time.perf_counter()
        frame = store.frame_at(game_id, t)
        if frame is None:
            abort(404)
        frame_ms = (time.perf_counter() - started) * 1000
        frame["ring"] = ring_at(game_id, frame["t"])
        frame["live"] = {"heldout": live.at(game_id, frame["t"], HELD_OUT), "final": live.at(game_id, frame["t"], FINAL)}
        total_ms = (time.perf_counter() - started) * 1000
        response = jsonify(frame)
        response.headers["Server-Timing"] = f"frame;dur={frame_ms:.1f}, total;dur={total_ms:.1f}"
        return response

    @app.get("/api/matches/<int:game_id>/curve")
    def match_curve(game_id: int):
        return jsonify(store.curve_points(game_id))

    @app.get("/api/matches/<int:game_id>/heldout")
    def match_heldout(game_id: int):
        return jsonify(store.heldout_points(game_id))

    @app.get("/api/evaluation")
    def evaluation():
        return jsonify(store.evaluation())

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Apex WinLab dashboard.")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with collection/ and raw/.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--backend", choices=("auto", "files", "tiger"), default="auto")
    args = parser.parse_args(argv)
    if not args.data_dir.is_dir():
        raise SystemExit(f"--data-dir is not a directory: {args.data_dir}")
    store, info = select_store(args.data_dir, args.backend)
    print(f"data backend: {info['label']} ({info['detail']})")
    app = create_app(args.data_dir, store=store, info=info)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
