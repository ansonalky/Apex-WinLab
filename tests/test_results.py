"""Final standings rows, identity, and the performance-page queries."""

from __future__ import annotations

import json
import unittest
from collections import defaultdict
from pathlib import Path

from app import create_app
from result_rows import game_is_comparable, load_results, whole
from stats_query import PLAYER_DETAIL, PLAYER_LEADERBOARD, TEAM_DETAIL, TEAM_LEADERBOARD, ranking

DATA = Path(__file__).resolve().parents[1] / "data"
CPR = "62978,127928,739030"


def scoped(rows, squad_format="trios", scoring="compatible", min_games=5, map_name=None, session_id=None):
    chosen = []
    for row in rows:
        if squad_format not in (None, "all") and row["squad_format"] != squad_format:
            continue
        if map_name and row["map_name"] != map_name:
            continue
        if session_id and row["session_id"] != session_id:
            continue
        if scoring == "compatible" and not row["points_comparable"]:
            continue
        if scoring == "not_compatible" and row["points_comparable"]:
            continue
        chosen.append(row)
    groups = defaultdict(list)
    for row in chosen:
        groups[row["roster_key"]].append(row)
    return {key: value for key, value in groups.items() if len(value) >= min_games}


class ResultRowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (DATA / "collection" / "manifest.json").is_file():
            raise unittest.SkipTest("telemetry is not on this machine")
        cls.teams, cls.players = load_results(DATA)

    def test_missing_stat_stays_null(self):
        self.assertIsNone(whole(None))
        self.assertIsNone(whole(1.5))
        self.assertEqual(whole(3.0), 3)

    def test_each_team_once_and_reimport_is_stable(self):
        keys = [(row["game_id"], row["roster_key"]) for row in self.teams]
        self.assertEqual(len(keys), len(set(keys)))
        again, players_again = load_results(DATA)
        self.assertEqual(keys, [(row["game_id"], row["roster_key"]) for row in again])
        self.assertEqual(len(self.players), len(players_again))

    def test_team_id_and_shared_names_do_not_merge_rosters(self):
        by_id = defaultdict(set)
        by_name = defaultdict(set)
        for row in self.teams:
            by_id[row["team_id"]].add(row["roster_key"])
            by_name[(row["squad_format"], row["team_name"])].add(row["roster_key"])
        self.assertTrue(any(len(rosters) > 1 for rosters in by_id.values()))
        self.assertTrue(any(len(rosters) > 1 for rosters in by_name.values()))

    def test_cpr_matches_its_standings_lines(self):
        rows = [row for row in self.teams if row["roster_key"] == CPR]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["session_id"] for row in rows}, {23257, 23240, 23220})
        self.assertEqual(len(rows), len({row["game_id"] for row in rows}))
        cache = json.loads((DATA / "collection" / "cache" / "stats-23257.json").read_text(encoding="utf-8"))
        game = next(item for item in cache["games"] if item["id"] == 123570)
        team = next(item for item in game["teams"] if item["name"] == "CPR")
        overall = team["overall_stats"]
        stored = next(row for row in rows if row["game_id"] == 123570)
        self.assertEqual(stored["placement"], overall["teamPlacement"])
        self.assertEqual(stored["kills"], overall["kills"])
        self.assertEqual(stored["damage"], overall["damageDealt"])
        self.assertEqual(stored["score"], overall["score"])
        self.assertEqual(stored["score"], 9 + overall["kills"])
        self.assertNotEqual(stored["score"], overall["score"] + overall["kills"])
        self.assertAlmostEqual(sum(row["placement"] for row in rows) / 6, 9.833333333333334)
        self.assertAlmostEqual(sum(row["kills"] for row in rows) / 6, 3.0)
        self.assertAlmostEqual(sum(row["damage"] for row in rows) / 6, 3110.8333333333335)
        self.assertAlmostEqual(sum(row["score"] for row in rows) / 6, 6.333333333333333)
        self.assertEqual(sum(row["placement"] == 1 for row in rows), 0)
        self.assertEqual(sum(row["placement"] <= 5 for row in rows), 2)

    def test_incompatible_games_are_not_marked_comparable(self):
        by_game = defaultdict(list)
        for row in self.teams:
            by_game[row["game_id"]].append(row)
        self.assertTrue(all(row["points_comparable"] for row in by_game[123349]))
        self.assertTrue(all(not row["points_comparable"] for row in by_game[123425]))
        self.assertFalse(game_is_comparable([]))

    def test_filters_recompute_inside_the_filtered_set(self):
        compatible = scoped(self.teams, "trios", "compatible", 5)
        duos = scoped(self.teams, "duos", "compatible", 5)
        one_map = scoped(self.teams, "trios", "compatible", 1, map_name="mp_rr_desertlands_mu5")
        self.assertIn(CPR, compatible)
        self.assertEqual(duos, {})
        self.assertNotEqual(
            sum(row["score"] for row in compatible[CPR]) / len(compatible[CPR]),
            sum(row["score"] for row in one_map[CPR]) / len(one_map[CPR]),
        )

    def test_queries_do_not_join_frames(self):
        for statement in (TEAM_LEADERBOARD, TEAM_DETAIL, PLAYER_LEADERBOARD, PLAYER_DETAIL):
            self.assertNotIn("player_frames", statement)
            self.assertNotIn("score + kills", statement)
        self.assertIn("AVG(s.score)", TEAM_LEADERBOARD)
        allow, rank, note = ranking({"squad_format": "trios", "scoring": "compatible"})
        self.assertTrue(allow)
        self.assertEqual(rank, "avg_points")
        self.assertIn("not added", note)
        allow, rank, _note = ranking({"squad_format": "all", "scoring": "compatible"})
        self.assertFalse(allow)
        self.assertEqual(rank, "avg_placement")

    def test_stats_page_reports_a_missing_database(self):
        app = create_app(DATA if DATA.is_dir() else Path("."), backend="files")
        client = app.test_client()
        page = client.get("/stats")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Performance in collected matches", page.data)
        api = client.get("/api/stats/teams")
        self.assertEqual(api.status_code, 503)
        self.assertNotIn(b"postgres://", api.data)


if __name__ == "__main__":
    unittest.main()
