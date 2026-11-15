"""Event timeline stays display-only and does not invent a ring end."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from event_timeline import display_feed, markers_for_team, ring_span

DATA = Path(__file__).resolve().parents[1] / "data"
GAME = 123633


class FeedLabels(unittest.TestCase):
    def test_kills_knocks_and_squad_wipes_stay_distinct(self):
        series = [{
            "t": 10,
            "f": [
                {"type": "down", "player": {"name": "A", "teamId": 1}, "victim": {"name": "B", "teamId": 2}},
                {"type": "down", "derived": True, "player": {"name": "A", "teamId": 1}, "victim": {"name": "C", "teamId": 2}},
                {"type": "kill", "credit": True, "player": {"name": "A", "teamId": 1}, "victim": {"name": "C", "teamId": 2}, "weapon": "Volt"},
                {"type": "kill", "credit": False, "player": {"name": "World", "teamId": 0}, "victim": {"name": "D", "teamId": 1}, "weapon": "The Ring"},
                {"type": "eliminated", "team": [{"name": "D", "teamId": 1}, {"name": "E", "teamId": 1}]},
            ],
        }]
        feed = display_feed(series)
        self.assertEqual([row["type"] for row in feed], ["knock", "kill", "kill", "squad_elimination"])
        self.assertTrue(feed[1]["confirmed"])
        self.assertFalse(feed[2]["confirmed"])
        markers = markers_for_team(feed, 1, lambda t: t)
        kinds = [marker["kind"] for marker in markers]
        self.assertEqual(kinds, ["knock", "kill", "death", "squad_elimination"])
        self.assertTrue(markers[1]["text"].startswith("Confirmed kill"))
        self.assertTrue(markers[0]["text"].startswith("Knock"))
        self.assertIn("not a confirmed kill", markers[2]["text"])
        self.assertEqual(markers[3]["text"], "Squad eliminated")

    def test_missing_ring_end_is_not_filled(self):
        span = ring_span({"stage": 5, "start": 100, "startCenter": {"x": 0, "y": 0}, "startSize": 10})
        self.assertFalse(span["end_recorded"])
        self.assertIsNone(span["end_t"])
        recorded = ring_span({
            "stage": 0, "start": 10, "end": 20,
            "startCenter": {"x": 0, "y": 0}, "endCenter": {"x": 1, "y": 1},
            "startSize": 10, "endSize": 5,
        })
        self.assertEqual(recorded["end_t"], 20)


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class RealReplay(unittest.TestCase):
    def test_final_ring_has_no_invented_end_and_coach_does_not_see_the_timeline(self):
        from coach import build_packet
        from dashboard_data import MatchStore
        from live_predictions import LivePredictions
        from tactical import TacticalIndex

        index = TacticalIndex(DATA)
        timeline = index.event_timeline(GAME, 20)
        self.assertTrue(timeline["display_only"])
        unfinished = [ring for ring in timeline["rings"] if not ring["end_recorded"]]
        self.assertTrue(unfinished)
        self.assertTrue(all(ring["end_t"] is None and ring["end_elapsed"] is None for ring in unfinished))
        kinds = {marker["kind"] for marker in timeline["markers"]}
        self.assertIn("kill", kinds)
        self.assertIn("knock", kinds)
        self.assertTrue(all(marker["text"].split(" · ")[0] in {"Knock", "Confirmed kill", "Death", "Squad eliminated"} for marker in timeline["markers"]))
        game = index.game(GAME)
        self.assertNotIn("kill", {event["type"] for event in game["events"]})
        tactical = index.at(GAME, timeline["playing_start"] + 250, 20)
        self.assertNotIn("target", tactical["ring"])
        self.assertNotIn("markers", tactical)
        store = MatchStore(DATA)
        packet = build_packet(store=store, live=LivePredictions(DATA), tactical=index, game_id=GAME, t=timeline["playing_start"] + 250, team=20)
        blob = json.dumps(packet)
        self.assertNotIn("display_feed", blob)
        self.assertNotIn("event-timeline", blob)
        self.assertNotIn("Confirmed kill", blob)
        event_types = {item["data"]["type"] for item in packet["packet"]["items"] if item.get("kind") == "event"}
        self.assertNotIn("kill", event_types)
        self.assertNotIn("death", event_types)
