"""Result replacement is limited to the games selected for that import."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from psycopg.pq import TransactionStatus

from import_results import replace_results


def team(game_id: int, name: str, roster: str | None = None) -> dict:
    return {
        "game_id": game_id,
        "roster_key": roster if roster is not None else f"roster-{game_id}",
        "team_name": name,
    }


def player(game_id: int, player_id: int) -> dict:
    return {"game_id": game_id, "player_id": player_id, "player_name": str(player_id)}


class MemoryCursor:
    def __init__(self, connection: "MemoryConnection"):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.connection.statements.append(text)
        selected = set(params[0]) if params else set()
        if text == "DELETE FROM player_results WHERE game_id = ANY(%s)":
            self.connection.players = [row for row in self.connection.players if row["game_id"] not in selected]
            return
        if text == "DELETE FROM team_results WHERE game_id = ANY(%s)":
            self.connection.teams = [row for row in self.connection.teams if row["game_id"] not in selected]
            return
        raise AssertionError(text)

    def executemany(self, sql, rows):
        text = " ".join(sql.split())
        self.connection.statements.append(text)
        rows = list(rows)
        if self.connection.fail_inserts:
            raise RuntimeError("insert failed")
        if text.startswith("INSERT INTO team_results"):
            self.connection.teams.extend(rows)
            return
        if text.startswith("INSERT INTO player_results"):
            self.connection.players.extend(rows)
            return
        raise AssertionError(text)


class MemoryConnection:
    def __init__(self):
        self.teams: list[dict] = []
        self.players: list[dict] = []
        self.statements: list[str] = []
        self.fail_inserts = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def cursor(self):
        return MemoryCursor(self)

    @contextmanager
    def transaction(self):
        if self.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("nested transaction")
        self.info.transaction_status = TransactionStatus.INTRANS
        snapshot = (list(self.teams), list(self.players), list(self.statements))
        try:
            yield
        except Exception:
            self.teams, self.players, self.statements = snapshot
            self.info.transaction_status = TransactionStatus.IDLE
            raise
        else:
            self.info.transaction_status = TransactionStatus.IDLE


class ReplaceResultsTest(unittest.TestCase):
    def test_expansion_import_preserves_other_games_and_reimport_has_no_duplicates(self):
        connection = MemoryConnection()
        connection.teams = [team(101, "Original A"), team(102, "Original B")]
        connection.players = [player(101, 1), player(102, 2)]
        expansion_teams = [team(201, "Expansion A", "9,10"), team(201, "Expansion B", "11,12")]
        expansion_players = [player(201, 9), player(201, 10), player(201, 11)]

        replace_results(connection, expansion_teams, expansion_players, [201])
        self.assertEqual(
            [(row["game_id"], row["team_name"]) for row in connection.teams],
            [(101, "Original A"), (102, "Original B"), (201, "Expansion A"), (201, "Expansion B")],
        )
        self.assertEqual([row["player_id"] for row in connection.players if row["game_id"] != 201], [1, 2])

        replace_results(connection, expansion_teams, expansion_players, [201])
        expansion_team_rows = [row for row in connection.teams if row["game_id"] == 201]
        expansion_player_rows = [row for row in connection.players if row["game_id"] == 201]
        self.assertEqual(len(expansion_team_rows), len({row["roster_key"] for row in expansion_team_rows}))
        self.assertEqual(len(expansion_player_rows), len({row["player_id"] for row in expansion_player_rows}))
        self.assertEqual(len(expansion_team_rows), 2)
        self.assertEqual(len(expansion_player_rows), 3)
        self.assertEqual([row["team_name"] for row in connection.teams if row["game_id"] in (101, 102)], ["Original A", "Original B"])
        self.assertTrue(all("WHERE game_id = ANY" in sql for sql in connection.statements if sql.startswith("DELETE")))
        self.assertNotIn("DELETE FROM player_results", [sql for sql in connection.statements if "WHERE" not in sql])

    def test_empty_selection_deletes_nothing(self):
        connection = MemoryConnection()
        connection.teams = [team(101, "Original A")]
        connection.players = [player(101, 1)]
        replace_results(connection, [], [], [])
        self.assertEqual(connection.teams, [team(101, "Original A")])
        self.assertEqual(connection.players, [player(101, 1)])
        self.assertEqual(connection.statements, [])

    def test_rows_outside_the_selection_are_refused_before_delete(self):
        connection = MemoryConnection()
        connection.teams = [team(101, "Original A")]
        with self.assertRaises(ValueError):
            replace_results(connection, [team(201, "Expansion")], [], [])
        self.assertEqual(connection.teams, [team(101, "Original A")])
        self.assertEqual(connection.statements, [])

    def test_failed_insert_restores_the_selected_games_rows(self):
        connection = MemoryConnection()
        connection.teams = [team(101, "Original A"), team(201, "Old expansion")]
        connection.players = [player(101, 1), player(201, 9)]
        connection.fail_inserts = True
        with self.assertRaises(RuntimeError):
            replace_results(connection, [team(201, "New expansion")], [player(201, 10)], [201])
        self.assertEqual([row["team_name"] for row in connection.teams], ["Original A", "Old expansion"])
        self.assertEqual([row["player_id"] for row in connection.players], [1, 9])

    def test_open_transaction_deletes_nothing(self):
        connection = MemoryConnection()
        connection.info.transaction_status = TransactionStatus.INTRANS
        connection.teams = [team(101, "Original A")]
        with self.assertRaises(RuntimeError):
            replace_results(connection, [], [], [101])
        self.assertEqual(connection.teams, [team(101, "Original A")])
        self.assertEqual(connection.statements, [])


if __name__ == "__main__":
    unittest.main()
