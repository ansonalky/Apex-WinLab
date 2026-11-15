#!/usr/bin/env python3
"""Apex WinLab match dashboard.

Launch from the repository root, pointing at the telemetry directory:

    .venv/bin/python app.py --data-dir data --port 5050 --backend auto

``--backend files`` reads local files. ``--backend tiger`` reads Tiger Data
through ``TigerReader`` and fails if the database cannot be used.
``--backend auto`` uses Tiger Data when ``DATABASE_URL`` connects and holds
imported matches, else local files. The active backend, and why, is served at
``/api/backend`` and shown in Model details.

``--expansion-dir`` adds another accepted-game directory. Those games are not
given a frozen split or a held-out label. A new-match forward pass of the
unchanged ``models/model.json`` is shown instead. Maps, rings, and coaching
still read that directory's own replay files.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

from coach import Coach, build_packet
from dashboard_data import MatchStore, clear_expansion_dirs, expansion_dirs, register_expansion_dir
from decision_review import DECISION, build_decision_packet
from live_predictions import FINAL, HELD_OUT, NEW_MATCH, LivePredictions
from open_match import ApiClient, open_match
from preview_store import PreviewStore, cleanup as cleanup_previews
from preview_store import configure as configure_previews
from turning_points import LABEL, HighlightCache, canonical_squad
from map_api import map_bp, ring_at
from tactical import TacticalIndex
from stats_api import stats_bp


class FallbackStore:
    """Saved games from the primary store, then local files, then a temporary preview."""

    def __init__(self, primary, preview: PreviewStore, files=None):
        self.primary = primary
        self.preview = preview
        self.files = files
        self._choice: dict[int, object] = {}

    def _source(self, game_id: int):
        cached = self._choice.get(game_id)
        if cached is not None:
            return cached
        if self.primary.summary(game_id) is not None:
            self._choice[game_id] = self.primary
            return self.primary
        if self.files is not None and self.files.summary(game_id) is not None:
            self._choice[game_id] = self.files
            return self.files
        from preview_store import contains

        if contains(game_id):
            self._choice[game_id] = self.preview
            return self.preview
        return self.primary

    def summary(self, game_id: int):
        return self._source(game_id).summary(game_id)

    def frame_at(self, game_id: int, t: int):
        return self._source(game_id).frame_at(game_id, t)

    def frame(self, game_id: int, index: int):
        source = self._source(game_id)
        if source is self.preview:
            built = self.preview.summary(game_id)
            times = (built or {}).get("times") or []
            if not times:
                return None
            index = max(0, min(index, len(times) - 1))
            return self.preview.frame_at(game_id, times[index]["t"])
        return source.frame(game_id, index)

    def curve(self, game_id: int):
        source = self._source(game_id)
        return source.curve(game_id) if source is not self.preview else self.preview.curve(game_id)

    def heldout(self, game_id: int):
        source = self._source(game_id)
        return source.heldout(game_id) if source is not self.preview else self.preview.heldout(game_id)

    def curve_points(self, game_id: int):
        source = self._source(game_id)
        if source is self.preview:
            return self.preview.curve(game_id)
        return source.curve_points(game_id)

    def heldout_points(self, game_id: int):
        source = self._source(game_id)
        if source is self.preview:
            return self.preview.heldout(game_id)
        return source.heldout_points(game_id)

    def matches(self):
        return self.primary.matches()

    def __getattr__(self, name):
        return getattr(self.primary, name)


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
    root = data_dir.resolve()
    imports = root / "user-imports"
    if (imports / "collection" / "manifest.json").is_file():
        register_expansion_dir(imports)
    configure_previews(root / "previews")
    cleanup_previews()
    files = None if isinstance(store, MatchStore) else MatchStore(root)
    store = FallbackStore(store, PreviewStore(), files)
    app.config["DATA_BACKEND"] = info
    # The map and ring endpoints read local replay files under either backend.
    app.config["DATA_DIR"] = root
    app.register_blueprint(map_bp)
    app.register_blueprint(stats_bp)
    # Per-second predictions are computed from the local replay and the frozen
    # model files under either backend, and cached in memory.
    live = LivePredictions(root)
    # Same tables as the replay. The key is game, kind, squad, and model sha256.
    highlights = HighlightCache()
    # Explanatory tactical measurements from the local replay; not a model input.
    tactical = TacticalIndex(root)
    # AI coach: runs only on request, from a packet of evidence up to the chosen second.
    coach = Coach(root / "coach_cache")

    def highlight_error(game_id: int, kind: str, reason: str) -> dict:
        return {
            "available": False,
            "label": LABEL,
            "game_id": game_id,
            "kind": kind or None,
            "error": reason,
            "missing": [reason],
            "up": [],
            "down": [],
        }

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
        summary = dict(summary)
        summary["inference"] = live.inference(game_id)

        def warm() -> None:
            for kind in (HELD_OUT, FINAL):
                if live.available(game_id, kind):
                    live.table(game_id, kind)
            if live.available(game_id, NEW_MATCH):
                live.schedule(game_id)

        threading.Thread(target=warm, daemon=True).start()
        summary["live"] = {
            "heldout": live.model(HELD_OUT)["provenance"] if live.available(game_id, HELD_OUT) else None,
            "final": live.model(FINAL)["provenance"] if live.available(game_id, FINAL) else None,
            "new_match": (live.inference(game_id).get("provenance") if live.available(game_id, NEW_MATCH) else None),
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
        # Recorded facing (yaw) at this exact second, from the local replay. Display only.
        try:
            frame["facing"] = tactical.facing_at(game_id, frame["t"])
        except Exception:  # never let facing break a frame
            frame["facing"] = {"fresh_s": None, "players": {}}
        frame["live"] = {
            "heldout": live.at(game_id, frame["t"], HELD_OUT),
            "final": live.at(game_id, frame["t"], FINAL),
            "new_match": live.peek(game_id, frame["t"]),
        }
        # Squads whose eliminated event is at or before this second, for the roster display.
        frame["eliminated_squads"] = {
            str(team): t for team, t in live.squad_eliminations(game_id).items() if t is not None and t <= frame["t"]
        }
        total_ms = (time.perf_counter() - started) * 1000
        response = jsonify(frame)
        response.headers["Server-Timing"] = f"frame;dur={frame_ms:.1f}, total;dur={total_ms:.1f}"
        return response

    @app.get("/api/matches/<int:game_id>/highlights")
    def match_highlights(game_id: int):
        """Largest estimate changes for one squad and one prediction kind.

        Held-out and final-model lists are separate requests. The recorded
        result is not included; ``highlights_for_game`` stops at the cut-off.
        """
        kind = request.args.get("kind", "")
        team = request.args.get("team", "")
        if kind not in (HELD_OUT, FINAL, NEW_MATCH):
            return jsonify(highlight_error(game_id, kind, "kind must be held_out, final_model_fitted, or new_match")), 400
        if kind == NEW_MATCH and live.available(game_id, kind) and not live.ready(game_id, kind):
            live.schedule(game_id)
            return jsonify({
                "available": False,
                "status": "loading",
                "label": LABEL,
                "game_id": game_id,
                "kind": kind,
                "missing": ["Computing win estimates from the saved model."],
                "up": [],
                "down": [],
            })
        if not team.strip():
            return jsonify(highlight_error(game_id, kind, "team is required")), 400
        summary = store.summary(game_id)
        if summary is None:
            abort(404)
        squad_id = canonical_squad(team)
        known = {canonical_squad(squad.get("teamId")) for squad in summary.get("squads") or []}
        if squad_id not in known:
            return jsonify(highlight_error(game_id, kind, "this squad is not in this match"))
        return jsonify(highlights.get(root, game_id, kind, squad_id, live=live))

    @app.get("/api/matches/<int:game_id>/tactical")
    def match_tactical(game_id: int):
        try:
            t = int(request.args["t"])
        except (KeyError, ValueError):
            return jsonify({"error": "t is required"}), 400
        team = request.args.get("team")
        if not team:
            return jsonify({"error": "team is required"}), 400
        return jsonify(tactical.at(game_id, t, team))

    def coach_packet(game_id: int, t: object, team: object) -> tuple[dict, int]:
        try:
            t = int(t)
        except (TypeError, ValueError):
            return {"status": "invalid", "reason": "t is required"}, 400
        if not team:
            return {"status": "invalid", "reason": "team is required"}, 400
        return build_packet(store=store, live=live, tactical=tactical, game_id=game_id, t=t, team=team), 200

    @app.get("/api/coach/status")
    def coach_status():
        return jsonify(coach.status())

    @app.get("/api/matches/<int:game_id>/coach/packet")
    def match_coach_packet(game_id: int):
        built, code = coach_packet(game_id, request.args.get("t"), request.args.get("team"))
        return jsonify(built), code

    @app.post("/api/matches/<int:game_id>/coach")
    def match_coach(game_id: int):
        body = request.get_json(silent=True) or {}
        built, code = coach_packet(game_id, body.get("t"), body.get("team"))
        if built.get("status") != "ok":
            return jsonify(built), code
        return jsonify(coach.analyze(built["packet"]) | {"packet": built["packet"]})

    def decision_packet(game_id: int, args: dict) -> tuple[dict, int]:
        try:
            start_t, episode_start_t = int(args.get("start_t")), int(args.get("episode_start_t"))
            decision_t = None if args.get("decision_t") in (None, "") else int(args.get("decision_t"))
        except (TypeError, ValueError):
            return {"status": "invalid", "reason": "start_t and episode_start_t are required integers"}, 400
        team = args.get("team")
        if not team:
            return {"status": "invalid", "reason": "team is required"}, 400
        built = build_decision_packet(store=store, live=live, tactical=tactical, highlights=highlights, root=root,
                                      game_id=game_id, team=team, kind=args.get("kind", ""), start_t=start_t,
                                      episode_start_t=episode_start_t, decision_t=decision_t)
        return built, 400 if built.get("status") == "invalid" else 200

    @app.get("/api/matches/<int:game_id>/decision-review/packet")
    def match_decision_packet(game_id: int):
        built, code = decision_packet(game_id, request.args)
        return jsonify(built), code

    @app.post("/api/matches/<int:game_id>/decision-review")
    def match_decision_review(game_id: int):
        """AI review of one downward episode at a decision time; runs only on request."""
        built, code = decision_packet(game_id, request.get_json(silent=True) or {})
        if built.get("status") != "ok":
            return jsonify(built), code
        return jsonify(coach.analyze(built["packet"], DECISION) | {"packet": built["packet"], "suggested_t": built["suggested_t"]})

    @app.post("/api/open-match")
    def open_overstat():
        body = request.get_json(silent=True) or {}
        game_number = body.get("game_number")
        try:
            game_number = None if game_number in (None, "") else int(game_number)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Choose a game number from the list."}), 400
        result = open_match(
            body.get("url") or "",
            data_dirs=[root, *expansion_dirs(), root / "user-imports"],
            import_dir=root / "user-imports",
            cache_dir=root / "open-cache",
            client=ApiClient(),
            game_number=game_number,
            database="auto",
        )
        code = 200 if result.get("status") in ("ready", "exists", "choose", "retry") else 400
        return jsonify(result), code

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
    parser.add_argument(
        "--expansion-dir",
        type=Path,
        action="append",
        default=[],
        help="Extra accepted-game directory. Not part of the frozen dataset.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--backend", choices=("auto", "files", "tiger"), default="auto")
    args = parser.parse_args(argv)
    if not args.data_dir.is_dir():
        raise SystemExit(f"--data-dir is not a directory: {args.data_dir}")
    clear_expansion_dirs()
    for path in args.expansion_dir:
        register_expansion_dir(path)
    store, info = select_store(args.data_dir, args.backend)
    print(f"data backend: {info['label']} ({info['detail']})")
    app = create_app(args.data_dir, store=store, info=info)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
