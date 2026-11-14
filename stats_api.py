"""Performance page. Reads Tiger Data and does not serve replay controls."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from import_tiger import connect, redact
from stats_query import filter_options, player_detail, player_leaderboard, team_detail, team_leaderboard

stats_bp = Blueprint("stats", __name__)


def _filters() -> dict:
    return {
        "map_name": request.args.get("map") or None,
        "session_id": request.args.get("session") or None,
        "squad_format": request.args.get("format") or "trios",
        "scoring": request.args.get("scoring") or "verified",
        "min_games": request.args.get("min_games") or 5,
    }


def _run(fn):
    try:
        connection = connect()
    except SystemExit as exc:
        return jsonify({"ok": False, "error": redact(str(exc))}), 503
    try:
        return jsonify(fn(connection))
    except Exception as exc:
        line = str(exc).splitlines()[-1] if str(exc) else type(exc).__name__
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {redact(line)}"}), 503
    finally:
        connection.close()


@stats_bp.get("/stats")
def stats_page():
    return render_template("stats.html")


@stats_bp.get("/api/stats/options")
def options():
    return _run(filter_options)


@stats_bp.get("/api/stats/teams")
def teams():
    filters = _filters()
    return _run(lambda connection: team_leaderboard(connection, filters))


@stats_bp.get("/api/stats/teams/<path:roster_key>")
def team(roster_key: str):
    filters = _filters()
    return _run(lambda connection: team_detail(connection, roster_key, filters))


@stats_bp.get("/api/stats/players")
def players():
    filters = _filters()
    return _run(lambda connection: player_leaderboard(connection, filters))


@stats_bp.get("/api/stats/players/<int:player_id>")
def player(player_id: int):
    filters = _filters()
    return _run(lambda connection: player_detail(connection, player_id, filters))
