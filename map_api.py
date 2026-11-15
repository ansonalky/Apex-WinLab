"""Map config and current-ring state. Not the prediction or database adapter."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from map_config import current_ring, load_rings, public_config
from ring_label import NEXT_TO_CLOSE, ring_label

map_bp = Blueprint("map_view", __name__)
_rings: dict[int, list] = {}


def _data_dir() -> Path:
    return current_app.config["DATA_DIR"]


def _rings_for(game_id: int) -> list:
    if game_id not in _rings:
        _rings[game_id] = load_rings(_data_dir(), game_id)
    return _rings[game_id]


def ring_at(game_id: int, t: int) -> dict:
    state = current_ring(_rings_for(game_id), t)
    # Shared user-facing label; the raw ``stage`` (next ring to close while waiting) is kept.
    state.update(ring_label(state.get("state"), state.get("stage"), NEXT_TO_CLOSE))
    state["t"] = t
    state["show_future"] = False
    return state


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
    return jsonify(ring_at(game_id, t))
