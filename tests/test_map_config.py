"""Map transform and ring motion copied from the Overstat replay bundle."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from map_config import MAP_SIZE, current_ring, map_cord_r, map_cord_x, map_cord_y, map_key, map_spec


class TransformTest(unittest.TestCase):
    def test_worlds_edge_origin_is_the_image_center(self):
        spec = MAP_SIZE["worlds_edge"]
        self.assertEqual(map_cord_x(0, spec), 1024)
        self.assertEqual(map_cord_y(0, spec), 1024)
        self.assertEqual(map_cord_x(22500, spec), 1536)
        self.assertEqual(map_cord_y(22500, spec), 512)

    def test_olympus_offset_is_not_guessed_from_players(self):
        spec = MAP_SIZE["olympus"]
        self.assertAlmostEqual(map_cord_x(0, spec), 1024 * (8000 / 45000) + 1024)
        self.assertAlmostEqual(map_cord_y(0, spec), 1024 * (2000 / 44000) + 1024)

    def test_direct_ids_keep_their_own_size(self):
        self.assertEqual(map_key("mp_rr_district_mu1"), "mp_rr_district_mu1")
        self.assertEqual(map_key("mp_rr_desertlands_mu5"), "worlds_edge")
        self.assertIsNone(map_key("mp_rr_district_mu1_night"))
        self.assertIsNone(map_spec("mp_rr_district_mu1_night"))

    def test_cached_images_match_telemetry_ids(self):
        for map_id in (
            "mp_rr_desertlands_mu5",
            "mp_rr_district_mu1",
            "mp_rr_olympus_mu3",
            "mp_rr_tropic_island_mu2_landscape",
        ):
            spec = map_spec(map_id)
            self.assertIsNotNone(spec)
            self.assertTrue(spec["image"].endswith(f"{map_id}.webp"))

    def test_worlds_edge_ring_matches_the_replay_formula(self):
        data = Path(__file__).resolve().parents[1] / "data"
        manifest = data / "collection" / "manifest.json"
        if not manifest.is_file():
            self.skipTest("telemetry is not on this machine")
        document = json.loads(manifest.read_text(encoding="utf-8"))
        record = next(row for row in document["games"].values() if row.get("game_id") == 123349)
        payload = json.loads((Path.cwd() / record["download_path"]).read_text(encoding="utf-8-sig"))
        rings = payload["match"]["rings"]
        waiting = current_ring(rings, 100)
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["stage"], 0)
        self.assertEqual(waiting["countdown_s"], 125)
        self.assertEqual(int(waiting["location"]["x"]), -7820)
        closing = current_ring(rings, 355)
        self.assertEqual(closing["state"], "closing")
        self.assertEqual(closing["countdown_s"], 130)
        start_r = rings[0]["startSize"]
        end_r = rings[0]["endSize"]
        self.assertAlmostEqual(closing["location"]["r"], start_r - 0.5 * (start_r - end_r))
        self.assertNotIn("future", closing)


if __name__ == "__main__":
    unittest.main()
