#!/usr/bin/env python3
"""Apex WinLab match dashboard.

Launch from the repository root, pointing at the telemetry directory:

    .venv/bin/python app.py --data-dir /path/to/data
"""

from __future__ import annotations

import argparse
from pathlib import Path

from flask import Flask, abort, jsonify, render_template

from dashboard_data import MatchStore, evaluation_panel


def create_app(data_dir: Path) -> Flask:
    app = Flask(__name__)
    store = MatchStore(data_dir.resolve())
    app.config["DATA_DIR"] = store.data_dir

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/matches")
    def matches():
        return jsonify(store.matches())

    @app.get("/api/matches/<int:game_id>")
    def match_summary(game_id: int):
        summary = store.summary(game_id)
        if summary is None:
            abort(404)
        return jsonify(summary)

    @app.get("/api/matches/<int:game_id>/frames/<int:index>")
    def match_frame(game_id: int, index: int):
        frame = store.frame(game_id, index)
        if frame is None:
            abort(404)
        return jsonify(frame)

    @app.get("/api/matches/<int:game_id>/curve")
    def match_curve(game_id: int):
        return jsonify(store.curve_points(game_id))

    @app.get("/api/matches/<int:game_id>/heldout")
    def match_heldout(game_id: int):
        return jsonify(store.heldout_points(game_id))

    @app.get("/api/evaluation")
    def evaluation():
        return jsonify(evaluation_panel(store.data_dir))

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Apex WinLab dashboard.")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with collection/ and raw/.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)
    if not args.data_dir.is_dir():
        raise SystemExit(f"--data-dir is not a directory: {args.data_dir}")
    app = create_app(args.data_dir)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
