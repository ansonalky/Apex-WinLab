"""Overstat map and ring math from the public replay bundle.

Source: https://overstat.gg/js/9107.be2636c4.js
MAP_SIZE table: https://overstat.gg/js/9090.a1b18f4e.js (webpack module 7170)
Short names: the same 9090 bundle, getMapNameShort.

Images are /maps_2048/<telemetry map id>.webp, 2048 by 2048.
A map id is unsupported when it has no MAP_SIZE entry or no matching image.
Another map's image is never substituted.
"""

from __future__ import annotations

import json
from pathlib import Path

# Exact literals from MAP_SIZE. 52e3 is 52000, 43e3 is 43000, 4e4 is 40000.
MAP_SIZE = {
    "storm_point": {"x": 52000, "y": 52000, "ox": 0, "oy": 0},
    "worlds_edge": {"x": 45000, "y": 45000, "ox": 0, "oy": 0},
    "e-district": {"x": 42500, "y": 43000, "ox": 0, "oy": 0},
    "olympus": {"x": 45000, "y": 44000, "ox": 8000, "oy": -2000},
    "kings_canyon": {"x": 40750, "y": 40750, "ox": -3250, "oy": -3250},
    "broken_moon": {"x": 40000, "y": 40000, "ox": 0, "oy": 0},
    "mp_rr_district_mu1": {"x": 42500, "y": 43000, "ox": 0, "oy": 0},
    "mp_rr_tropic_island_mu2": {"x": 51000, "y": 51000, "ox": -500, "oy": -1000},
    "mp_rr_tropic_island_mu2_landscape": {"x": 51000, "y": 51000, "ox": -500, "oy": -1000},
    "mp_rr_tropic_island_mu2_landscape_breach": {"x": 51000, "y": 51000, "ox": -500, "oy": -1000},
}

# getMapNameShort table. Missing ids return the id itself, then fail the size lookup.
SHORT_NAMES = {
    "mp_rr_canyonlands_hu": "Kings Canyon",
    "mp_rr_canyonlands_hu_v2": "Kings Canyon",
    "mp_rr_tropic_island": "Storm Point",
    "mp_rr_tropic_island_mu1": "Storm Point",
    "mp_rr_tropic_island_mu2": "Storm Point",
    "mp_rr_tropic_island_mu2_landscape": "Storm Point",
    "mp_rr_tropic_island_mu1_storm_lc": "Storm Point",
    "mp_rr_tropic_island_mu1_storm": "Storm Point",
    "mp_rr_desertlands_mu3": "Worlds Edge",
    "mp_rr_desertlands_mu4": "Worlds Edge",
    "mp_rr_olympus_mu2": "Olympus",
    "mp_rr_olympus_mu2_v2": "Olympus",
    "mp_rr_divided_moon": "Broken Moon",
    "mp_rr_divided_moon_mu1": "Broken Moon",
    "mp_rr_divided_moon_mu1_v2": "Broken Moon",
    "mp_rr_desertlands": "Worlds Edge",
    "mp_rr_desertlands_hu": "Worlds Edge",
    "mp_rr_desertlands_hu_lc": "Worlds Edge",
    "draft_worldsedge": "Worlds Edge",
    "draft_stormpoint": "Storm Point (ALGS)",
    "mp_rr_district": "E-District",
    "mp_rr_district_mu1": "E-District",
    "draft_edistrict": "E-District",
    "draft_worldsedge_v2": "Worlds Edge (ALGS)",
    "draft_stormpoint_v2": "Storm Point (ALGS)",
    "draft_edistrict_v2": "E-District (ALGS)",
    "draft_edistrict_v3": "E-District (ALGS)",
    "draft_brokenmoon": "Broken Moon (ALGS)",
    "draft_brokenmoon_v2": "Broken Moon (ALGS)",
    "kc_season_1": "Kings Canyon (Season 1)",
    "draft_kingscanyon": "Kings Canyon (ALGS)",
    "draft_olympus": "Olympus (ALGS)",
    "draft_olympus_v2": "Olympus (ALGS)",
    "mp_rr_olympus_mu3_breach": "Olympus",
    "mp_rr_district_mu1_breach": "E-District",
    "mp_rr_desertlands_hu_breach": "Worlds Edge",
    "mp_rr_tropic_island_mu2_landscape_breach": "Storm Point",
    "mp_rr_olympus_mu3": "Olympus",
    "mp_rr_desertlands_mu5": "Worlds Edge",
    "draft_worldsedge_v3": "Worlds Edge",
}

SVG = 2048
HALF = 1024
IMAGE_SOURCE = "https://overstat.gg/maps_2048/{map_id}.webp"
ASSET_DIR = Path(__file__).resolve().parent / "static" / "maps"


def map_key(map_id: str) -> str | None:
    """Same key the replay uses: the id if it is in MAP_SIZE, else the short name."""
    if map_id in MAP_SIZE:
        return map_id
    short = SHORT_NAMES.get(map_id, map_id)
    key = short.lower().replace(" ", "_", 1)
    if key in MAP_SIZE:
        return key
    return None


def image_path(map_id: str) -> Path | None:
    path = ASSET_DIR / f"{map_id}.webp"
    return path if path.is_file() else None


def map_spec(map_id: str) -> dict | None:
    key = map_key(map_id)
    image = image_path(map_id)
    if key is None or image is None:
        return None
    spec = dict(MAP_SIZE[key])
    spec["key"] = key
    spec["map_id"] = map_id
    spec["image"] = f"/static/maps/{map_id}.webp"
    return spec


def map_cord_x(value: float, spec: dict) -> float:
    value = int(value)
    return HALF * ((value + spec["ox"]) / spec["x"]) + HALF


def map_cord_y(value: float, spec: dict) -> float:
    value = int(value)
    return HALF * ((-value - spec["oy"]) / spec["y"]) + HALF


def map_cord_r(value: float, spec: dict) -> float:
    return HALF * (value / spec["x"])


def current_ring(rings: list[dict], t: int) -> dict:
    """Replay currentRingClosing / nextRing / currentRingLocation at clock t.

    Only the current circle is returned. Future stage centers are not included.
    A stage the replay ends inside has no ``end`` or ``endCenter``. While it
    closes, no circle is returned: its size and center are not recorded.
    """
    ordered = sorted(rings, key=lambda ring: (ring.get("start") or 0, ring.get("stage") or 0))
    unfinished = next(
        (ring for ring in ordered if ring.get("start") is not None and ring["start"] < t
         and (ring.get("end") is None or ring.get("endCenter") is None)),
        None,
    )
    if unfinished is not None:
        return {
            "state": "closing_unrecorded",
            "stage": unfinished.get("stage"),
            "countdown_s": None,
            "location": None,
            "show_future": False,
        }
    closing = next((ring for ring in ordered if ring["start"] < t and ring.get("end") is not None and ring["end"] >= t), None)
    nxt = next((ring for ring in ordered if ring["start"] > t), None)
    stage = next((ring for ring in ordered if ring.get("end") is None or ring["end"] > t), ordered[-1] if ordered else None)
    if closing is None:
        if nxt is not None:
            location = {
                "x": nxt["startCenter"]["x"],
                "y": nxt["startCenter"]["y"],
                "r": nxt["startSize"],
            }
            return {
                "state": "waiting",
                "stage": stage.get("stage") if stage else nxt.get("stage"),
                "countdown_s": nxt["start"] - t,
                "location": location,
                "show_future": False,
            }
        return {
            "state": "none",
            "stage": stage.get("stage") if stage else None,
            "countdown_s": None,
            "location": None,
            "show_future": False,
        }
    progress = (t - closing["start"]) / (closing["end"] - closing["start"])
    start = closing["startCenter"]
    end = closing["endCenter"]
    location = {
        "x": start["x"] + (end["x"] - start["x"]) * progress,
        "y": start["y"] + (end["y"] - start["y"]) * progress,
        "r": closing["startSize"] - (closing["startSize"] - closing["endSize"]) * progress,
    }
    return {
        "state": "closing",
        "stage": closing.get("stage"),
        "countdown_s": closing["end"] - t,
        "location": location,
        "show_future": False,
    }


def public_config(map_id: str) -> dict:
    spec = map_spec(map_id)
    if spec is None:
        return {
            "supported": False,
            "map_id": map_id,
            "message": f"Map background unavailable for {map_id}: no matching MAP_SIZE entry or 2048 image. Positions are not drawn on another map.",
        }
    return {
        "supported": True,
        "map_id": map_id,
        "key": spec["key"],
        "x": spec["x"],
        "y": spec["y"],
        "ox": spec["ox"],
        "oy": spec["oy"],
        "image": spec["image"],
        "source": IMAGE_SOURCE.format(map_id=map_id),
        "attribution": "Map image from the public Overstat replay, /maps_2048.",
    }


def load_rings(data_dir: Path, game_id: int) -> list[dict]:
    from dashboard_data import _raw_path, _record

    record = _record(data_dir, game_id)
    path = _raw_path(record, data_dir) if record else None
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rings = (payload.get("match") or {}).get("rings") or []
    return rings if isinstance(rings, list) else []
