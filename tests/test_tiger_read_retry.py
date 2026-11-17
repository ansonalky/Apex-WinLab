"""TigerReader retries a read once when the server ended its connection, and nothing else."""

import threading
import unittest
from unittest import mock

import psycopg

from tiger_read import TigerReader


class FakeConnection:
    """Fails its first ``failures`` executes with ``error``; ``lose`` marks it broken like psycopg does."""

    def __init__(self, error=None, lose=True):
        self.error = error
        self.lose = lose
        self.closed = False
        self.broken = False
        self.executed = 0

    def execute(self, query, params=None):
        self.executed += 1
        if self.error is not None:
            if self.lose:
                self.closed = self.broken = True
            raise self.error
        return self

    def fetchall(self):
        return [(1, "Red")]

    def close(self):
        self.closed = True


def reader_with(connections):
    reader = TigerReader.__new__(TigerReader)
    reader._dsn = "postgresql://example.invalid/db"
    reader._lock = threading.Lock()
    reader._conn = connections.pop(0)
    connect = mock.patch("psycopg.connect", side_effect=lambda *a, **k: connections.pop(0))
    return reader, connect


DROPPED = psycopg.OperationalError("consuming input failed: terminating connection due to administrator command")


class Retry(unittest.TestCase):
    def test_dropped_connection_reconnects_and_retries_once(self):
        stale, fresh = FakeConnection(DROPPED), FakeConnection()
        reader, connect = reader_with([stale, fresh])
        with connect as patched:
            self.assertEqual(reader.list_squads(1), [{"team_id": 1, "team_name": "Red"}])
        self.assertEqual(patched.call_count, 1)
        self.assertEqual((stale.executed, fresh.executed), (1, 1))

    def test_dropped_twice_raises_the_original_error(self):
        reader, connect = reader_with([FakeConnection(DROPPED), FakeConnection(DROPPED)])
        with connect as patched, self.assertRaises(psycopg.OperationalError) as caught:
            reader.list_squads(1)
        self.assertIn("administrator command", str(caught.exception))
        self.assertEqual(patched.call_count, 1)  # one reconnect, no third attempt

    def test_query_error_on_a_live_connection_is_not_retried(self):
        timeout = psycopg.OperationalError("canceling statement due to statement timeout")
        live = FakeConnection(timeout, lose=False)
        reader, connect = reader_with([live, FakeConnection()])
        with connect as patched, self.assertRaises(psycopg.OperationalError):
            reader.list_squads(1)
        self.assertEqual(patched.call_count, 0)
        self.assertEqual(live.executed, 1)

    def test_sql_errors_are_not_retried(self):
        bad = FakeConnection(psycopg.ProgrammingError("syntax error"), lose=False)
        reader, connect = reader_with([bad, FakeConnection()])
        with connect as patched, self.assertRaises(psycopg.ProgrammingError):
            reader.list_squads(1)
        self.assertEqual(patched.call_count, 0)
