import copy
import json
import tempfile
import unittest
from pathlib import Path

from coach import Coach, _clock
from decision_review import (DECISION, build_decision_packet, decision_schema, default_decision_t, validate_decision,
                             verify_decision_packet)

DATA = Path(__file__).resolve().parent.parent / "data"
GAME, TEAM = 123633, "11"


def item(i, t, data, start=100):
    return {"id": i, "kind": "x", "label": i, "t": t, "elapsed": t - start, "clock": _clock(t - start), "window": None, "data": data}


def tiny_packet() -> dict:
    return {
        "packet_version": 1, "game_id": 1, "team": "11", "squad_name": "Squad", "t": 400, "elapsed": 300, "clock": "05:00",
        "window": {"start_t": 340, "end_t": 400, "start_clock": "04:00", "end_clock": "05:00", "seconds": 60},
        "decision": {"t": 400, "clock": "05:00", "label": "Suggested review time, not a proven mistake time."},
        "items": [
            item("FIRE-IN", 400, {"from_opponents": 323, "attackers": [{"team": "19", "damage": 323, "sector": "NE"}]}),
            item("SEP", 400, {"max_separation": 702}),
            item("OPP-19", 400, {"distance": 2100, "sector": "NE"}),
            item("REF-MATE-1", 400, {"name": "Alel", "position": {"x": 18592, "y": 5463}}),
            item("OBSERVER", 400, {"note": "observer-only"}),
        ],
        "retrospective": {"from_t": 400, "to_t": 440, "from_clock": "05:00", "to_clock": "05:40", "note": "after", "items": [
            item("R-EPISODE", 410, {"representative_window": {"before_pct": 9.3, "after_pct": 0.0}}),
            item("R-OWN", 440, {"model_features": {"players_alive": {"start": 2, "end": 0}}}),
        ]},
    }


def good_review() -> dict:
    return {
        "drop_attribution": {"category": "own_squad_deterioration",
                             "explanation": "The estimate fell from 9.3% to 0% as both players went down.",
                             "evidence_ids": ["R-EPISODE", "R-OWN"]},
        "weaknesses": [{"name": "Split squad under fire", "observed": [
            {"fact": "Teammates were 702 u apart while taking 323 damage from the NE.", "evidence_ids": ["SEP", "FIRE-IN"]}],
            "interpretation": "Being split may have made it harder to trade.", "uncertainty": "Terrain between them is unknown.",
            "evidence_ids": ["SEP"]}],
        "options": [
            {"option": "regroup", "destination_ref": "REF-MATE-1",
             "evidence": [{"fact": "Alel's position is known.", "evidence_ids": ["REF-MATE-1"]}],
             "expected_benefit": "Two players able to trade together.", "cost": "Moving while under fire.",
             "uncertainty": "Whether the route has cover is unknown.",
             "observer_only": {"depends": False, "explanation": "Teammate positions are known to the squad."}},
            {"option": "hold", "destination_ref": "none",
             "evidence": [{"fact": "323 damage from the NE in the window.", "evidence_ids": ["FIRE-IN"]}],
             "expected_benefit": "No exposure from moving.", "cost": "Stays split.", "uncertainty": "Attacker position now unknown.",
             "observer_only": {"depends": True, "explanation": "The attacker's distance is observer-only."}},
        ],
        "recommendation": {"choice": "regroup", "condition": "If the route to Alel is not exposed to the NE attackers",
                           "rationale": "Regrouping addresses the separation.", "evidence_ids": ["SEP", "REF-MATE-1"]},
        "practice_takeaway": "Review spacing calls when the first damage arrives.",
        "missing_information": ["Terrain and cover between teammates."],
    }


class ValidateDecision(unittest.TestCase):
    def test_accepts_grounded_review(self):
        self.assertEqual(validate_decision(good_review(), tiny_packet()), [])

    def assert_rejects(self, review, fragment):
        problems = validate_decision(review, tiny_packet())
        self.assertTrue(any(fragment in p for p in problems), problems)

    def test_rejects_retrospective_evidence_for_recommendation(self):
        r = good_review()
        r["recommendation"]["evidence_ids"] = ["R-OWN"]
        self.assert_rejects(r, "not evidence at or before the decision time")
        r = good_review()
        r["options"][0]["evidence"][0]["evidence_ids"] = ["R-EPISODE"]
        self.assert_rejects(r, "not evidence at or before the decision time")

    def test_rotation_needs_known_destination(self):
        r = good_review()
        r["options"][1]["option"] = "rotate_to_verified_position"
        self.assert_rejects(r, "rotates without a verified destination")
        r["options"][1]["destination_ref"] = "OPP-19"
        self.assert_rejects(r, "has no known coordinates")

    def test_rejects_probability_for_unplayed_option(self):
        r = good_review()
        r["options"][0]["expected_benefit"] = "Raises the win probability."
        self.assert_rejects(r, "probability to an unplayed alternative")

    def test_rejects_recommendation_not_compared(self):
        r = good_review()
        r["recommendation"]["choice"] = "disengage"
        self.assert_rejects(r, "not one of the compared options")

    def test_rejects_unhedged_cover_or_route(self):
        r = good_review()
        r["options"][0]["cost"] = "The building to the east gives cover."
        self.assert_rejects(r, "states that buildings / cover exists")

    def test_rejects_invented_numbers_and_too_many_options(self):
        r = good_review()
        r["weaknesses"][0]["interpretation"] = "They were 1,500 u too far apart."
        r["options"] = r["options"] + [dict(r["options"][1], option="disengage"), dict(r["options"][1], option="hold")]
        problems = validate_decision(r, tiny_packet())
        self.assertTrue(any("'1,500'" in p for p in problems))
        self.assertTrue(any("4 options" in p for p in problems))
        self.assertTrue(any("compared twice" in p for p in problems))

    def test_abstain_is_allowed(self):
        r = good_review()
        r["recommendation"] = {"choice": "abstain", "condition": "", "rationale": "The evidence is too thin.", "evidence_ids": []}
        self.assertEqual(validate_decision(r, tiny_packet()), [])

    def test_schema_separates_evidence_sets(self):
        schema = decision_schema(tiny_packet())
        props = schema["properties"]
        rec_ids = props["recommendation"]["properties"]["evidence_ids"]["items"]["enum"]
        attr_ids = props["drop_attribution"]["properties"]["evidence_ids"]["items"]["enum"]
        self.assertNotIn("R-OWN", rec_ids)
        self.assertIn("R-OWN", attr_ids)
        self.assertEqual(props["options"]["items"]["properties"]["destination_ref"]["enum"], ["REF-MATE-1", "none"])


class VerifyDecisionPacket(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(verify_decision_packet(tiny_packet()), [])

    def test_catches_leaks(self):
        p = tiny_packet()
        p["items"].append(item("LATE", 401, {}))
        p["retrospective"]["items"].append(item("R-LATER", 441, {}))
        p["retrospective"]["items"].append(item("R-X", 420, {"placement": 5}))
        p["decision"]["episode_end_t"] = 440
        problems = verify_decision_packet(p)
        for fragment in ("LATE", "R-LATER", "placement", "episode_end_t"):
            self.assertTrue(any(fragment in x for x in problems), (fragment, problems))

    def test_default_decision_time(self):
        self.assertEqual(default_decision_t(906, 69), 896)
        self.assertEqual(default_decision_t(72, 69), 69)

    def test_coach_runs_decision_spec_and_never_caches_rejects(self):
        env = {"COACH_PROVIDER": "anthropic", "COACH_MODEL": "test-model", "ANTHROPIC_API_KEY": "x"}
        bad = good_review()
        bad["recommendation"]["evidence_ids"] = ["R-OWN"]
        replies = [(bad, {}), (good_review(), {})]
        seen = []

        def fake(config, request):
            seen.append(request)
            return replies.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            c = Coach(Path(tmp), call=fake, env=env)
            self.assertEqual(c.analyze(tiny_packet(), DECISION)["status"], "rejected")
            ok = c.analyze(tiny_packet(), DECISION)
            self.assertEqual((ok["status"], ok["prompt_version"]), ("ok", "decision-v2"))
            self.assertTrue(c.analyze(tiny_packet(), DECISION)["cached"])
        self.assertIn("retrospective", seen[0]["system"])
        self.assertIn("REF-MATE-1", json.dumps(seen[0]["schema"]))


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class RealEpisode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dashboard_data import MatchStore
        from live_predictions import HELD_OUT, LivePredictions
        from tactical import TacticalIndex
        from turning_points import HighlightCache

        root = DATA.resolve()
        cls.deps = dict(store=MatchStore(root), live=LivePredictions(root), tactical=TacticalIndex(root),
                        highlights=HighlightCache(), root=root)
        cls.kind = HELD_OUT
        cls.episode = cls.deps["highlights"].get(root, GAME, HELD_OUT, 11, live=cls.deps["live"])["down"][0]

    def build(self, decision_t=None, kind=None):
        return build_decision_packet(**self.deps, game_id=GAME, team=TEAM, kind=kind or self.kind,
                                     start_t=self.episode["start_t"], episode_start_t=self.episode["episode_start_t"],
                                     decision_t=decision_t)

    def test_default_packet_is_split_at_the_decision_time(self):
        built = self.build()
        self.assertEqual(built["status"], "ok")
        p = built["packet"]
        self.assertEqual(p["t"], self.episode["episode_start_t"] - 10)
        self.assertEqual(verify_decision_packet(p), [])
        for i in p["items"]:
            self.assertLessEqual(i["t"], p["t"], i["id"])
        retro = p["retrospective"]["items"]
        self.assertTrue(all(i["t"] <= self.episode["episode_end_t"] for i in retro))
        self.assertTrue(any(i["id"].startswith("R-EV") and i["data"]["type"] == "knock" for i in retro))
        ids = {i["id"] for i in p["items"]}
        self.assertTrue({"SEP", "MOVE", "REF-SQUAD", "OBSERVER", "ELEV", "FIRE-IN", "RING"} <= ids)
        self.assertTrue(any(i.startswith("REF-MATE-") for i in ids))
        pre_text = json.dumps({k: v for k, v in p.items() if k != "retrospective"})
        # As keys: the limitation text may say "no winner".
        for word in ("winner", "placement", "conclusion", "episode_end", "episode_start", "suggested"):
            self.assertNotIn(f'"{word}', pre_text)
        self.assertNotIn('"winner', json.dumps(p["retrospective"]))

    def test_edited_time_and_bounds(self):
        self.assertEqual(self.build(self.episode["episode_start_t"] - 40)["packet"]["t"], self.episode["episode_start_t"] - 40)
        self.assertEqual(self.build(self.episode["episode_end_t"])["status"], "invalid")
        self.assertEqual(self.build(10)["status"], "invalid")

    def test_unknown_episode_rejected(self):
        built = build_decision_packet(**self.deps, game_id=GAME, team=TEAM, kind=self.kind, start_t=1, episode_start_t=2, decision_t=None)
        self.assertEqual(built["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
