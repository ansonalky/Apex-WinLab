"""Start-here preset and review loading copy. No provider calls."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dashboard_data import MatchStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATIC = ROOT / "static"


def _js(source: str) -> str:
    path = Path(tempfile.mkstemp(suffix=".js")[1])
    path.write_text(source, encoding="utf-8")
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class StartHereMoment(unittest.TestCase):
    @unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
    def test_eqt_is_alive_at_18_47_and_knocked_at_18_48(self):
        store = MatchStore(DATA)
        summary = store.summary(123633)
        squad = next(item for item in summary["squads"] if str(item["teamId"]) == "20")
        self.assertEqual(squad["teamName"], "EQT")
        elapsed = 18 * 60 + 47
        start = summary["playing_start"]
        self.assertLessEqual(elapsed, summary["last_t"] - start)
        before = store.frame_at(123633, start + elapsed)
        after = store.frame_at(123633, start + elapsed + 1)
        self.assertEqual(before["elapsed"], elapsed)
        eqt = [player for player in before["players"] if str(player["teamId"]) == "20"]
        self.assertTrue(eqt)
        self.assertTrue(all(player["status"] == "alive" for player in eqt))
        naggi = next(player for player in after["players"] if player["name"] == "NAGGI")
        self.assertEqual(naggi["status"], "downed")

    @unittest.skipUnless((DATA / "raw").is_dir(), "local replay data not present")
    def test_demo_frame_and_direct_ring_match_at_18_47(self):
        from app import create_app
        client = create_app(DATA, backend="files").test_client()
        summary = client.get("/api/matches/123633").get_json()
        t = summary["playing_start"] + 1127
        frame = client.get(f"/api/matches/123633/at/{t}").get_json()
        ring = client.get(f"/api/matches/123633/ring?t={t}").get_json()
        self.assertEqual(frame["elapsed"], 1127)
        self.assertEqual(frame["ring"], ring)
        self.assertEqual(ring["state"], "closing_target_unknown")
        self.assertIsNone(ring["location"])
        self.assertIsNone(ring["target"])


class StartHerePage(unittest.TestCase):
    def test_demo_is_explicit_and_deep_links_do_not_request_a_review(self):
        page = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="start-here"', page)
        self.assertIn("Opens an example and loads a coach review.", page)
        self.assertIn('id="ai-announcer"', page)
        self.assertIn('aria-live="polite"', page)
        script = (STATIC / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('const START_GAME = "123633"', script)
        self.assertIn('const START_TEAM = "20"', script)
        self.assertIn("const START_ELAPSED = 18 * 60 + 47", script)
        self.assertIn("function linkedSliderTime", script)
        body = script.split("function startHere", 1)[1].split("async function loadMatch", 1)[0]
        self.assertNotIn("/coach", body)
        self.assertNotIn("decision-review", body)
        self.assertIn("window.Coach.start", body)
        self.assertIn("await seek(START_ELAPSED", body)
        self.assertIn("demoMomentConfirmed()", body)
        self.assertNotIn("DecisionReview", body)
        for name in ("dashboard.js", "coach.js", "decision.js", "review_status.js"):
            source = (STATIC / name).read_text(encoding="utf-8")
            parsed = _js(f"new Function({json.dumps(source)}); 'ok'")
            self.assertEqual(parsed, "ok", name)

    def test_review_status_covers_wait_failure_and_cache(self):
        library = (STATIC / "review_status.js").read_text(encoding="utf-8")
        probe = library + r"""
var calls = 0;
var cleared = 0;
globalThis.setInterval = function () { calls += 1; return 7; };
globalThis.clearInterval = function () { cleared += 1; };
var voice = ReviewStatus.announcer();
var ticks = [0, 1, 7, 8, 9].map(function (second) { return voice.tick(second); });
var stop = ReviewStatus.startClock(null, function () {});
stop();
stop();
JSON.stringify({
  ticks: ticks,
  calls: calls,
  cleared: cleared,
  timeout: ReviewStatus.classifyFailure({ reason: "timeout", aborted: true }),
  cancelled: ReviewStatus.classifyFailure({ reason: "cancel", aborted: true }),
  network: ReviewStatus.classifyFailure({ aborted: false }),
  cached: ReviewStatus.completionAnnouncement({ status: "ok", cached: true }),
  fresh: ReviewStatus.completionAnnouncement({ status: "ok", cached: false }),
  rejected: ReviewStatus.completionAnnouncement({ status: "error", kind: "rejected" }),
  markup: ReviewStatus.loadingMarkup("EQT", "18:47")
});
"""
        payload = json.loads(_js(probe))
        self.assertEqual(payload["ticks"][0]["text"], "Reviewing this moment… 0s")
        self.assertEqual(payload["ticks"][0]["announce"], "Reviewing this moment")
        self.assertIsNone(payload["ticks"][1]["announce"])
        self.assertIsNone(payload["ticks"][2]["announce"])
        self.assertFalse(payload["ticks"][2]["still"])
        self.assertEqual(payload["ticks"][3]["announce"], "Still reviewing…")
        self.assertIn("Still reviewing…", payload["ticks"][3]["text"])
        self.assertNotIn("minute", payload["ticks"][3]["text"].lower())
        self.assertIsNone(payload["ticks"][4]["announce"])
        self.assertTrue(payload["ticks"][4]["still"])
        self.assertEqual(payload["calls"], 1)
        self.assertEqual(payload["cleared"], 1)
        self.assertEqual(payload["timeout"]["kind"], "timeout")
        self.assertTrue(payload["timeout"]["retryable"])
        self.assertEqual(payload["cancelled"]["kind"], "cancelled")
        self.assertEqual(payload["network"]["kind"], "network")
        self.assertEqual(payload["network"]["error"], "The review could not reach the server.")
        self.assertEqual(payload["cached"], "Review ready, from cache")
        self.assertEqual(payload["fresh"], "Review ready")
        self.assertEqual(payload["rejected"], "Review failed")
        self.assertIn("Reviewing this moment…", payload["markup"])
        self.assertIn("EQT at 18:47", payload["markup"])
        self.assertIn('aria-hidden="true"', payload["markup"])

    def test_both_reviews_keep_one_request_and_clear_the_clock(self):
        coach = (STATIC / "coach.js").read_text(encoding="utf-8")
        decision = (STATIC / "decision.js").read_text(encoding="utf-8")
        for source in (coach, decision):
            self.assertIn("if (running", source)
            self.assertIn("ReviewStatus.loadingMarkup", source)
            self.assertIn("ReviewStatus.classifyFailure", source)
            self.assertIn("stopClock()", source)
            self.assertIn("if (running) return;", source)
        self.assertIn("button.disabled = true", coach)
        self.assertIn("setRunEnabled(false)", decision)
        self.assertIn("request.squadName", decision)
        self.assertIn('now.game) !== String(state.game)', decision)
