"""Quality filter, dedupe, and resume. No network."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from collection import decide, game_quality, mark_duplicates, quality_reason, session_quality
from collect_matches import fetch_telemetry


class QualityTest(unittest.TestCase):
    def test_threshold_is_inclusive_and_unknown_is_not_zero(self):
        self.assertIsNone(quality_reason(7.0, 7.0))
        self.assertEqual(quality_reason(6.999, 7.0), "below_quality_threshold")
        self.assertEqual(quality_reason(0.0, 7.0), "below_quality_threshold")
        self.assertEqual(quality_reason(None, 7.0), "quality_unknown")
        self.assertIsNone(game_quality({"analytics": {}}))
        self.assertEqual(game_quality({"analytics": {"qualityScore": 0}}), 0.0)

    def test_session_score_is_not_used_as_the_game_score(self):
        stats = {"analytics": {"qualityScore": 9.5}, "games": [{"id": 1, "analytics": {}}]}
        self.assertEqual(session_quality(stats), 9.5)
        self.assertIsNone(game_quality(stats["games"][0]))
        record = {"quality_score": None, "livedata_id": "abc", "download_status": "downloaded"}
        status, reason = decide(record, {"ok": True, "reason": "accepted"}, 7.0)
        self.assertEqual((status, reason), ("excluded", "quality_unknown"))

    def test_passing_score_still_needs_validation(self):
        record = {"quality_score": 8.0, "livedata_id": "abc", "download_status": "downloaded"}
        status, reason = decide(record, {"ok": False, "reason": "outcome_unknown"}, 7.0)
        self.assertEqual((status, reason), ("excluded", "outcome_unknown"))
        status, reason = decide(record, {"ok": True, "reason": "accepted"}, 7.0)
        self.assertEqual((status, reason), ("accepted", "accepted"))


class DedupeTest(unittest.TestCase):
    def test_same_livedata_on_two_game_ids_is_an_inconsistent_duplicate(self):
        rows = [
            {"game_id": 1, "livedata_id": "same", "mid": "m1"},
            {"game_id": 2, "livedata_id": "same", "mid": "m2"},
        ]
        mark_duplicates(rows)
        self.assertNotEqual(rows[0].get("reason"), "duplicate")
        self.assertEqual(rows[1]["reason"], "duplicate")
        self.assertIn("previously game 1", rows[1]["inconsistent_mapping"])


class ResumeTest(unittest.TestCase):
    def test_existing_raw_file_is_not_downloaded_again(self):
        class Boom:
            def get(self, url):
                raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw" / "abc.timeseries.json"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"{}")
            fetched = fetch_telemetry(
                {"livedata_id": "abc"},
                root,
                Boom(),
            )
        self.assertEqual(fetched["download_status"], "cached")
        self.assertEqual(fetched["sha256"], __import__("hashlib").sha256(b"{}").hexdigest())


if __name__ == "__main__":
    unittest.main()
