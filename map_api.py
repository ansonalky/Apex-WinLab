"""Map config and current-ring state. Not the prediction or database adapter."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from map_config import public_config

map_bp = Blueprint("map_view", __name__)


def ring_at(game_id: int, t: int) -> dict:
    """The map's ring at replay second ``t``.

    Read from live ring entries only, through the same reader and shared label
    as the tactical panel and the coach (``TacticalIndex.ring_overlay_at``). The
    raw file's planned schedule is not used.
    """
    return current_app.config["TACTICAL"].ring_overlay_at(game_id, t)


@map_bp.get("/api/map-config")
def map_config_route():
    map_id = request.args.get("map") or ""
    return jsonify(public_config(map_id))


@map_bp.get("/api/matches/<int:game_id>/ring")
def ring_route(game_id: int):
    try:
        t = int(request.args["t"])
    except (KeyError, ValueError):
        return jsonify({"error": "t is required"}), 400
    if request.args.get("observer") == "1":
        from observer_ring import ObserverRingIndex

        index = current_app.config.get("OBSERVER_RINGS")
        if index is None:
            index = ObserverRingIndex(current_app.config["TACTICAL"].data_dir)
            current_app.config["OBSERVER_RINGS"] = index
        return jsonify(index.at(game_id, t))
    return jsonify(ring_at(game_id, t))
