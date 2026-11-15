import unittest

from coach import terrain_claims, validate_analysis
from ring_label import LAST_CLOSED, NEXT_TO_CLOSE, ring_label
from tests.test_coach import good_analysis, tiny_packet
from tests.test_decision_review import good_review
from tests.test_decision_review import tiny_packet as decision_packet
from decision_review import validate_decision

# Sentences Gemini actually wrote on 2026-11-14 (game 123633), plus edge cases.
CLAIMS = [
    "Collapses the 702 unit gap so teammates can trade fire and share cover.",
    "Consider using the inside margin to hold cover or rotate away from the E/SE sectors.",
    "The building to the east gives cover.",
    "Meaningless holds the high ground over KARMA.",
    "The route to Alel is safe.",
    # A hedge elsewhere in the sentence does not cover an asserted clause.
    "Alel has cover, though the route may be exposed.",
]
ALLOWED = [
    "Abandons current positions without verified cover; moving away from NE risks drifting south.",
    "If a covered path exists between Alel and delete that avoids NE sightlines, regroup.",
    "Consider reviewing whether Meaningless has hard cover from the higher SE angle.",
    "Did the NE squad have an open sightline onto Meaningless's cover?",
    "Distance and bearing alone do not establish line of sight or blocked routes.",
    "Regrouping addresses the split, provided the path between teammates is safe.",
    "Height only; not high-ground control.",
    "Holding avoids crossing unknown open ground while already well inside the ring.",
]


class TerrainClaims(unittest.TestCase):
    def test_claims_are_found(self):
        for text in CLAIMS:
            self.assertTrue(terrain_claims(text), text)

    def test_conditional_hedged_and_questions_are_allowed(self):
        for text in ALLOWED:
            self.assertEqual(terrain_claims(text), [], text)

    def test_condition_field_makes_the_suggestion_conditional(self):
        text = "Consider breaking line of sight before healing."
        self.assertTrue(terrain_claims(text))
        self.assertEqual(terrain_claims(text, conditional=True), [])

    def test_same_standard_in_both_reviews(self):
        claim = "Teammates can share cover there."
        situation = good_analysis()
        situation["factors"][0]["interpretation"] = claim
        decision = good_review()
        decision["options"][0]["expected_benefit"] = claim
        for problems in (validate_analysis(situation, tiny_packet()), validate_decision(decision, decision_packet())):
            self.assertTrue(any("cover exists" in p for p in problems), problems)
        # A conditional suggestion passes in both.
        situation["factors"][0]["interpretation"] = "If cover exists there, teammates could trade together."
        decision["options"][0]["expected_benefit"] = "If cover exists there, teammates could trade together."
        self.assertFalse([p for p in validate_analysis(situation, tiny_packet()) if "exists" in p])
        self.assertFalse([p for p in validate_decision(decision, decision_packet()) if "exists" in p])

    def test_unknowns_are_not_claims(self):
        situation = good_analysis()
        situation["factors"][0]["uncertainty"] = "Terrain cover between the squads."
        situation["missing_information"] = ["Cover and sightlines between the squads."]
        self.assertEqual(validate_analysis(situation, tiny_packet()), [])


class RingLabel(unittest.TestCase):
    # Game 123633: raw stages 0-5; at t 896 the map says 3 (next) and ring_features 2 (last closed).
    def test_both_conventions_give_one_label(self):
        self.assertEqual(ring_label("waiting", 3, NEXT_TO_CLOSE)["label"], "Ring 3 closed · ring 4 next")
        self.assertEqual(ring_label("waiting", 2, LAST_CLOSED)["label"], "Ring 3 closed · ring 4 next")
        self.assertEqual(ring_label("closing", 3, NEXT_TO_CLOSE)["label"], ring_label("closing", 3, LAST_CLOSED)["label"])

    def test_raw_index_is_kept_with_its_meaning(self):
        out = ring_label("waiting", 2, LAST_CLOSED)
        self.assertEqual((out["stage_raw"], out["ring_number"], out["rings_closed"]), (2, 4, 3))
        self.assertIn("last ring that closed", out["stage_raw_meaning"])

    def test_first_ring_and_other_states(self):
        self.assertEqual(ring_label("closing", 0, LAST_CLOSED)["label"], "Ring 1 closing")
        self.assertEqual(ring_label("waiting", 0, NEXT_TO_CLOSE)["label"], "Waiting · ring 1 next")
        self.assertEqual(ring_label("closing_unrecorded", 5, NEXT_TO_CLOSE)["label"], "Ring 6 closing (end not recorded)")
        self.assertIsNone(ring_label("not_revealed", None, LAST_CLOSED)["ring_number"])


if __name__ == "__main__":
    unittest.main()
