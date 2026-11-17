"""Malformed live geometry must remain unknown, without inventing a circle."""
import copy
import unittest

from event_timeline import ring_span
from ring_features import GEOMETRY_KEYS, ring_at, ring_target

VALID = dict(stage=0, start=10, end=20, startCenter=dict(x=0, y=0),
             endCenter=dict(x=100, y=0), startSize=1000, endSize=500)


class InvalidRing(unittest.TestCase):
    def assert_unknown(self, entry):
        for t in (15, 25):
            ring = ring_at([entry], t)
            self.assertFalse(ring['known'])
            self.assertIsNone(ring_target([entry], ring))
        self.assertFalse(ring_span(entry)['end_recorded'])
        self.assertIsNone(ring_span(entry)['end_t'])

    def test_missing_null_and_invalid_fields(self):
        for key in GEOMETRY_KEYS:
            for value in (None, True, '20', float('nan'), float('inf'), {}, []):
                with self.subTest(key=key, value=value):
                    entry = copy.deepcopy(VALID)
                    entry[key] = value
                    self.assert_unknown(entry)
            entry = copy.deepcopy(VALID)
            del entry[key]
            self.assert_unknown(entry)

    def test_invalid_centers_radii_and_intervals(self):
        for key in ('startCenter', 'endCenter'):
            for center in ({'x': None, 'y': 1}, {'x': 0}, {'x': 0, 'y': float('nan')}):
                entry = copy.deepcopy(VALID)
                entry[key] = center
                self.assert_unknown(entry)
        for key, value in [('startSize', -1), ('endSize', -1), ('end', 10), ('end', 9)]:
            entry = copy.deepcopy(VALID)
            entry[key] = value
            self.assert_unknown(entry)

    def test_exact_boundaries_and_revealed_target(self):
        self.assertEqual(ring_at([VALID], 9)['phase'], 'inconsistent')
        self.assertEqual(ring_at([VALID], 10)['radius'], 1000)
        self.assertEqual(ring_at([VALID], 15)['radius'], 750)
        self.assertEqual(ring_at([VALID], 20)['phase'], 'waiting')
        self.assertEqual(ring_at([VALID], 20)['radius'], 500)
        self.assertIsNone(ring_target([VALID], ring_at([VALID], 20)))
