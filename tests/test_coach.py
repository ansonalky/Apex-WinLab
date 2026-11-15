import copy
import json
import tempfile
import unittest
from pathlib import Path

import coach
from coach import Coach, CoachError, build_packet, validate_analysis, verify_packet

DATA = Path(__file__).resolve().parent.parent / "data"
GAME, TEAM = 123633, 11
KNOCK_T, QUIET_T = 924, 760


def tiny_packet() -> dict:
    item = lambda i, t, data, window=None: {"id": i, "kind": "x", "label": i, "t": t, "elapsed": t - 100,
                                            "clock": coach._clock(t - 100), "window": window, "data": data}
    return {
        "packet_version": 1, "game_id": 1, "team": "11", "squad_name": "Squad", "t": 400, "elapsed": 300,
        "clock": "05:00", "window": {"start_t": 340, "end_t": 400, "start_clock": "04:00", "end_clock": "05:00", "seconds": 60},
        "items": [
            item("ELEV", 400, {"squad_z": 6528, "mean_dz": 120, "higher_than": 1, "lower_than": 3}),
            item("FIRE-10S", 400, {"damage_taken": 439, "window_s": 10}),
            item("PRED", 400, {"probability": 0.033, "probability_pct": 3.3}),
            item("EV-390-0", 390, {"type": "knock"}),
        ],
    }


def good_analysis() -> dict:
    return {
        "situation_summary": "The squad took 439 damage in 10 s while lower than 3 nearby squads. The estimate is 3.3%.",
        "factors": [{
            "name": "Incoming fire",
            "assessment": "disadvantage",
            "observed": [{"fact": "439 damage in the last 10 s, knock at 04:50.", "evidence_ids": ["FIRE-10S", "EV-390-0"]}],
            "interpretation": "Recent sustained fire suggests the squad was exposed.",
            "uncertainty": "Recent damage does not show current exposure.",
            "evidence_ids": ["FIRE-10S"],
        }],
        "recommendations": [{"type": "conditional_recommendation", "condition": "If the attackers still have sightlines",
                             "text": "consider breaking line of sight before healing.", "evidence_ids": ["FIRE-10S"]}],
        "missing_information": ["Terrain and cover between the squads."],
    }


class ValidateAnalysis(unittest.TestCase):
    def test_accepts_grounded_response(self):
        self.assertEqual(validate_analysis(good_analysis(), tiny_packet()), [])

    def test_rejects_nonexistent_evidence(self):
        a = good_analysis()
        a["factors"][0]["evidence_ids"] = ["OPP-99"]
        self.assertTrue(any("nonexistent evidence 'OPP-99'" in p for p in validate_analysis(a, tiny_packet())))

    def test_rejects_unsupported_number(self):
        a = good_analysis()
        a["factors"][0]["interpretation"] = "Holding here would raise the win chance by 12 points."
        self.assertTrue(any("'12'" in p for p in validate_analysis(a, tiny_packet())))

    def test_rejects_time_not_in_evidence(self):
        a = good_analysis()
        a["situation_summary"] = "At 07:42 the squad was pushed."
        self.assertTrue(any("07:42" in p for p in validate_analysis(a, tiny_packet())))

    def test_rejects_causal_and_outcome_claims(self):
        for text in ("Low ground caused the loss.", "They will win from here.", "The eventual winner was nearby.",
                     "Elevation contributed 3.3% to winning."):
            a = good_analysis()
            a["factors"][0]["interpretation"] = text
            self.assertTrue(validate_analysis(a, tiny_packet()), text)

    def test_rejects_length_limits(self):
        a = good_analysis()
        a["situation_summary"] = "One. Two. Three."
        a["factors"] = a["factors"] * 4
        a["recommendations"] = a["recommendations"] * 3
        problems = validate_analysis(a, tiny_packet())
        self.assertTrue(any("3 sentences" in p for p in problems))
        self.assertTrue(any("4 factors" in p for p in problems))
        self.assertTrue(any("3 recommendations" in p for p in problems))

    def test_rejects_unconditional_recommendation(self):
        a = good_analysis()
        a["recommendations"][0]["condition"] = ""
        self.assertTrue(any("not conditional" in p for p in validate_analysis(a, tiny_packet())))

    def test_allows_rounded_packet_numbers(self):
        a = good_analysis()
        a["factors"][0]["observed"][0]["fact"] = "The squad sits at about 6,500 u."
        self.assertEqual(validate_analysis(a, tiny_packet()), [])


class VerifyPacket(unittest.TestCase):
    def test_clean_packet(self):
        self.assertEqual(verify_packet(tiny_packet()), [])

    def test_catches_outcome_keys_and_future_items(self):
        p = tiny_packet()
        p["items"][0]["data"]["winner"] = {"teamId": 18}
        p["items"][1]["data"]["last_hit_t"] = 401
        p["items"].append(dict(p["items"][2], id="LATE", t=402))
        problems = verify_packet(p)
        self.assertTrue(any("winner" in x for x in problems))
        self.assertTrue(any("last_hit_t" in x for x in problems))
        self.assertTrue(any("LATE" in x for x in problems))


class CoachFlow(unittest.TestCase):
    env = {"COACH_PROVIDER": "anthropic", "COACH_MODEL": "test-model", "ANTHROPIC_API_KEY": "x"}

    def test_not_configured_makes_no_call(self):
        calls = []
        result = Coach(call=lambda c, p: calls.append(1), env={"COACH_MODEL": "m", "COACH_PROVIDER": "anthropic"}).analyze(tiny_packet())
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(calls, [])
        self.assertNotIn("analysis", result)

    def test_status_never_contains_key_value(self):
        status = Coach(env=self.env | {"ANTHROPIC_API_KEY": "sk-secret-value"}).status()
        self.assertNotIn("sk-secret-value", json.dumps(status))
        self.assertEqual(status["credential_source"], "ANTHROPIC_API_KEY")

    def test_caches_only_validated_results(self):
        calls = []

        def fake(config, packet):
            calls.append(config["model"])
            return good_analysis(), {"served_model": "test-model"}

        with tempfile.TemporaryDirectory() as tmp:
            c = Coach(Path(tmp), call=fake, env=self.env)
            first = c.analyze(tiny_packet())
            second = c.analyze(tiny_packet())
            self.assertEqual((first["status"], first["cached"]), ("ok", False))
            self.assertTrue(second["cached"])
            self.assertEqual(len(calls), 1)
            # A different model or packet is a different cache entry.
            Coach(Path(tmp), call=fake, env=self.env | {"COACH_MODEL": "other"}).analyze(tiny_packet())
            changed = tiny_packet()
            changed["items"][1]["data"]["damage_taken"] = 440
            c.analyze(changed)
            self.assertEqual(len(calls), 3)

    def test_rejected_and_failed_responses_are_not_cached_or_substituted(self):
        bad = good_analysis()
        bad["factors"][0]["evidence_ids"] = ["NOPE"]
        responses = [CoachError("timeout", "timed out", True), (bad, {}), (good_analysis(), {})]

        def fake(config, packet):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with tempfile.TemporaryDirectory() as tmp:
            c = Coach(Path(tmp), call=fake, env=self.env)
            timeout = c.analyze(tiny_packet())
            self.assertEqual((timeout["status"], timeout["kind"], timeout["retryable"]), ("error", "timeout", True))
            self.assertNotIn("analysis", timeout)
            rejected = c.analyze(tiny_packet())
            self.assertEqual(rejected["status"], "rejected")
            self.assertNotIn("analysis", rejected)
            self.assertEqual(c.analyze(tiny_packet())["status"], "ok")


@unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
class RealPackets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dashboard_data import MatchStore
        from live_predictions import LivePredictions
        from tactical import TacticalIndex

        root = DATA.resolve()
        cls.deps = {"store": MatchStore(root), "live": LivePredictions(root), "tactical": TacticalIndex(root)}
        cls.summary = cls.deps["store"].summary(GAME)

    def packet(self, t, team=TEAM):
        return build_packet(**self.deps, game_id=GAME, t=t, team=team)

    def assert_no_future(self, packet):
        self.assertEqual(verify_packet(packet), [])
        text = json.dumps(packet, ensure_ascii=False)
        for word in ("winner", "placement", "conclusion", "cutoff", "finished"):
            self.assertNotIn(f'"{word}', text)
        for item in packet["items"]:
            self.assertLessEqual(item["t"], packet["t"])

    def test_knock_moment(self):
        built = self.packet(KNOCK_T)
        self.assertEqual(built["status"], "ok")
        packet = built["packet"]
        self.assert_no_future(packet)
        events = [i for i in packet["items"] if i["kind"] == "event" and i["data"]["type"] == "knock"]
        self.assertTrue(any(e["t"] == KNOCK_T and e["data"]["side"] == "this squad was the target" for e in events))
        fire = next(i for i in packet["items"] if i["id"] == "FIRE-IN")["data"]
        self.assertGreater(fire["from_opponents"], 0)
        ids = [i["id"] for i in packet["items"]]
        self.assertEqual(ids, [i["id"] for i in self.packet(KNOCK_T)["packet"]["items"]], "IDs are stable")

    def test_quiet_moment(self):
        packet = self.packet(QUIET_T)["packet"]
        self.assert_no_future(packet)
        items = {i["id"]: i for i in packet["items"]}
        self.assertEqual(items["FIRE-IN"]["data"]["from_opponents"], 0)
        self.assertEqual(items["PRESSURE"]["data"]["near_squads"], 0)
        self.assertFalse([k for k in items if k.startswith("EV-")])
        self.assertIn("CHG-60", items)

    def test_final_model_probability_excluded(self):
        packet = self.packet(KNOCK_T)["packet"]
        pred = next(i for i in packet["items"] if i["id"] == "PRED")
        self.assertEqual(pred["data"]["provenance"]["kind"], "held_out")
        self.assertNotIn("final_model", json.dumps(packet))

    def test_finished_match_is_refused(self):
        built = self.packet(self.summary["conclusion_t"])
        self.assertEqual(built["status"], "finished")
        self.assertNotIn("packet", built)

    def test_route_without_key_returns_setup_and_packet(self):
        from app import create_app

        app = create_app(DATA, store=self.deps["store"], info={"backend": "files"})
        saved = {k: coach.os.environ.pop(k, None) for k in coach.CREDENTIAL_VARS}
        try:
            body = app.test_client().post(f"/api/matches/{GAME}/coach", json={"t": KNOCK_T, "team": TEAM}).json
        finally:
            coach.os.environ.update({k: v for k, v in saved.items() if v is not None})
        self.assertEqual(body["status"], "not_configured")
        self.assertNotIn("analysis", body)
        self.assertEqual(body["packet"]["t"], KNOCK_T)


if __name__ == "__main__":
    unittest.main()
