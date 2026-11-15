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
        "next_action": "If the attackers can still see you, break away and heal before re-engaging.",
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


class Providers(unittest.TestCase):
    def test_gemini_is_the_default(self):
        config = coach.load_config({})
        self.assertEqual(config["provider"], "gemini")
        self.assertTrue(config["model"])

    def test_no_fallback_to_anthropic(self):
        # An Anthropic key does not configure the Gemini provider.
        status = coach.provider_status(coach.load_config({}), {"ANTHROPIC_API_KEY": "x"})
        self.assertFalse(status["configured"])
        self.assertTrue(any("GEMINI_API_KEY" in m for m in status["missing"]))
        calls = []
        result = Coach(call=lambda c, r: calls.append(1), env={"ANTHROPIC_API_KEY": "x"}).analyze(tiny_packet())
        self.assertEqual((result["status"], result["provider"], calls), ("not_configured", "gemini", []))

    def test_gemini_status_reports_name_not_value(self):
        status = coach.provider_status(coach.load_config({}), {"GEMINI_API_KEY": "gem-secret"})
        self.assertTrue(status["configured"])
        self.assertEqual(status["credential_source"], "GEMINI_API_KEY")
        self.assertNotIn("gem-secret", json.dumps(status))

    def test_unknown_provider_is_an_error_not_a_fallback(self):
        with self.assertRaises(CoachError):
            coach.call_provider({"provider": "other"}, {})

    def test_cache_key_includes_provider_and_model(self):
        c = Coach(env={})
        base = {"provider": "gemini", "model": "m", "effort": "medium"}
        keys = {c.cache_key(tiny_packet(), base), c.cache_key(tiny_packet(), base | {"provider": "anthropic"}),
                c.cache_key(tiny_packet(), base | {"model": "n"})}
        self.assertEqual(len(keys), 3)


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
        # Not configured whichever provider is the default.
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


EXPANSION = Path.home() / "Apex-WinLab-expansion" / "data"


@unittest.skipUnless((DATA / "raw").is_dir() and (EXPANSION / "collection" / "manifest.json").is_file(),
                     "local replay data or the expansion package is not present")
class ExpansionPacket(unittest.TestCase):
    def test_expansion_game_is_a_verified_unseen_forward_pass(self):
        from dashboard_data import EXPANSION_REASON, MatchStore, clear_expansion_dirs, register_expansion_dir
        from live_predictions import LivePredictions
        from tactical import TacticalIndex

        register_expansion_dir(EXPANSION)
        try:
            root = DATA.resolve()
            store, live = MatchStore(root), LivePredictions(root)
            game = next(row for row in store.matches() if row["collection"] != "original")
            summary = store.summary(game["game_id"])
            team = summary["squads"][0]["teamId"]
            built = build_packet(store=store, live=live, tactical=TacticalIndex(root), game_id=game["game_id"],
                                 t=summary["playing_start"] + 120, team=team)
            self.assertEqual(built["status"], "ok")
            info = live.inference(game["game_id"])
            self.assertEqual(info["membership"], "verified_unseen", info.get("reason"))
            self.assertTrue(info["predict"])
            self.assertEqual(info["label"], "New-match prediction · model trained on the original 51 games.")
            self.assertFalse(info["provenance"]["held_out"])
            self.assertFalse(info["provenance"]["in_sample_game"])
            self.assertNotIn("fitted on this match", info["label"])
            self.assertEqual(store.curve(game["game_id"])["reason"], EXPANSION_REASON)
            self.assertEqual(store.heldout(game["game_id"])["reason"], EXPANSION_REASON)
        finally:
            clear_expansion_dirs()


class RejectionRecord(unittest.TestCase):
    env = {"COACH_PROVIDER": "anthropic", "COACH_MODEL": "test-model", "ANTHROPIC_API_KEY": "x"}

    def test_rejected_response_is_kept_for_review_and_never_cached(self):
        bad = good_analysis()
        bad["factors"][0]["interpretation"] = "Teammates can share cover there."
        with tempfile.TemporaryDirectory() as tmp:
            c = Coach(Path(tmp) / "coach_cache", call=lambda config, request: (bad, {"served_model": "test-model"}), env=self.env)
            result = c.analyze(tiny_packet())
            self.assertEqual(result["status"], "rejected")
            self.assertNotIn("analysis", result)
            saved = list((Path(tmp) / "coach_rejections").glob("*.json"))
            self.assertEqual(len(saved), 1)
            record = json.loads(saved[0].read_text())
            self.assertEqual(record["analysis"]["factors"][0]["interpretation"], "Teammates can share cover there.")
            self.assertTrue(any("cover exists" in p for p in record["problems"]))
            self.assertEqual(record["packet"]["clock"], "05:00")
            self.assertFalse((Path(tmp) / "coach_cache").exists() and any((Path(tmp) / "coach_cache").iterdir()))

    def test_accepted_response_is_not_recorded_as_a_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            Coach(Path(tmp) / "coach_cache", call=lambda c, r: (good_analysis(), {}), env=self.env).analyze(tiny_packet())
            self.assertFalse((Path(tmp) / "coach_rejections").exists())


@unittest.skipUnless((DATA / "previews").is_dir() and any((DATA / "previews").glob("*/meta.json")), "no local preview")
class PreviewReason(unittest.TestCase):
    def test_preview_summary_carries_the_recorded_reason(self):
        # Through the app route the page uses (the files store answers for previews).
        from app import create_app

        meta = json.loads(next((DATA / "previews").glob("*/meta.json")).read_text())
        response = create_app(DATA, backend="files").test_client().get(f"/api/matches/{meta['game_id']}")
        if response.status_code == 404:
            self.skipTest("preview expired")
        summary = response.get_json()
        self.assertEqual((summary["storage"], summary["acceptance_reason"]), ("preview", meta.get("reason")))


class RejectionRetention(unittest.TestCase):
    def test_old_and_excess_logs_are_pruned(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = 1_000_000_000.0
            for i in range(5):
                path = folder / f"r{i}.json"
                path.write_text("{}")
                os.utime(path, (now - i * 10, now - i * 10))
            stale = folder / "old.json"
            stale.write_text("{}")
            os.utime(stale, (now - 40 * 24 * 3600, now - 40 * 24 * 3600))
            coach.prune_rejections(folder, keep=3, max_age_s=30 * 24 * 3600, now=now)
            self.assertEqual(sorted(p.name for p in folder.glob("*.json")), ["r0.json", "r1.json", "r2.json"])

    def test_configured_key_values_are_scrubbed(self):
        import os

        bad = good_analysis()
        bad["factors"][0]["interpretation"] = "Teammates can share cover there."
        secret = "sk-test-SECRETVALUE-123"
        bad["situation_summary"] = f"Leaked {secret} here. The squad took 439 damage."
        saved_env = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = secret
        try:
            with tempfile.TemporaryDirectory() as tmp:
                c = Coach(Path(tmp) / "coach_cache", call=lambda config, request: (bad, {}),
                          env={"COACH_PROVIDER": "anthropic", "COACH_MODEL": "m", "ANTHROPIC_API_KEY": secret})
                self.assertEqual(c.analyze(tiny_packet())["status"], "rejected")
                text = next((Path(tmp) / "coach_rejections").glob("*.json")).read_text()
                self.assertNotIn(secret, text)
                self.assertIn("[redacted]", text)
        finally:
            if saved_env is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = saved_env
