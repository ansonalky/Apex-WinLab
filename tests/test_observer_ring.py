"""Observer schedules stay separate from causal ring and coach evidence."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from map_api import map_bp
from observer_ring import ObserverRingIndex
from ring_overlay import display_geometry
from tests.test_ring_transition import Transition, FIRST, NEXT, FINAL

THIRD = dict(stage=2,start=45,end=50,startCenter=dict(x=200,y=0),
             endCenter=dict(x=220,y=0),startSize=100,endSize=20)


class ObserverGeometry(unittest.TestCase):
    def test_countdown_and_closing_indexing(self):
        entries=[FIRST,NEXT,THIRD]
        for t, gray, dashed in [(5,0,1),(10,0,1),(19,0,1),(20,1,2),(21,1,2),
                                (29,1,2),(30,1,2),(39,1,2),(40,2,None),(45,2,None),(50,None,None)]:
            with self.subTest(t=t):
                overlay=display_geometry(entries,t,observer=True)
                self.assertEqual((overlay['target'] or {}).get('stage'),gray)
                self.assertEqual((overlay['following_target'] or {}).get('stage'),dashed)
                if gray is not None:
                    self.assertNotEqual(gray,dashed)
        a=display_geometry(entries,21,True)
        b=display_geometry(entries,29,True)
        self.assertEqual(a['location'],b['location'],'countdown boundary stays fixed')
        self.assertEqual(a['target'],b['target'])
        self.assertEqual(a['following_target'],b['following_target'])
        self.assertNotEqual(display_geometry(entries,31,True)['location'],display_geometry(entries,35,True)['location'])

    def test_invalid_missing_and_skipped_geometry_is_not_invented(self):
        for field in ('start','end','startCenter','endCenter','startSize','endSize'):
            for value in (None,True,'bad',float('nan'),float('inf')):
                bad=copy.deepcopy(NEXT);bad[field]=value
                with self.subTest(field=field,value=value):
                    overlay=display_geometry([FIRST,bad,THIRD],15,True)
                    self.assertEqual(overlay['target']['stage'],0)
                    self.assertIsNone(overlay['following_target'])
        self.assertIsNone(display_geometry([FIRST,THIRD],15,True)['following_target'])
        self.assertIsNone(display_geometry([FIRST,NEXT,FINAL],35,True)['following_target'])
        self.assertIsNone(display_geometry([FIRST,NEXT,FINAL],46,True)['location'])

    def test_following_revealed_stage_is_distinct_from_active(self):
        result=display_geometry([FIRST,NEXT],19)
        self.assertEqual(result['target']['stage'],0)
        self.assertEqual(result['following_target']['stage'],1)
        self.assertFalse(result['observer_preview'])


class EndpointIsolation(unittest.TestCase):
    build = Transition.build

    def test_observer_endpoint_uses_a_separate_index_and_respects_revisions(self):
        causal=self.build(reveal=25)
        observer=ObserverRingIndex(causal.data_dir)
        app=Flask(__name__);app.register_blueprint(map_bp)
        app.config['TACTICAL']=causal;app.config['OBSERVER_RINGS']=observer
        client=app.test_client()
        snapshot=copy.deepcopy(causal.game(1))
        with patch('observer_ring._record',return_value={'game_id':1}), patch('observer_ring._raw_path',return_value=causal.data_dir/'replay.json'):
            default=client.get('/api/matches/1/ring?t=21').get_json()
            preview=client.get('/api/matches/1/ring?t=21&observer=1').get_json()
            self.assertIsNone(default['target'])
            self.assertFalse(default['observer_preview'])
            self.assertTrue(preview['observer_preview'])
            self.assertEqual(preview['target']['stage'],1)
            self.assertEqual(preview['target']['x'],9999,'observer sees schedule recorded at t, not a later revision')
            revised=client.get('/api/matches/1/ring?t=25&observer=1').get_json()
            self.assertEqual(revised['target']['x'],200)
            backward=client.get('/api/matches/1/ring?t=21&observer=1').get_json()
            self.assertEqual(preview,backward)
            self.assertEqual(default,client.get('/api/matches/1/ring?t=21&observer=0').get_json())
        self.assertEqual(causal.game(1),snapshot,'observer request cannot mutate tactical snapshots')


@unittest.skipUnless((Path('data')/'raw').is_dir(),'local replay data unavailable')
class RealCoachIsolation(unittest.TestCase):
    def test_preview_cannot_change_coach_packet_or_predictions(self):
        from app import create_app
        client=create_app(Path('data').resolve(),backend='files').test_client()
        before=client.get('/api/matches/123633/coach/packet?t=944&team=20').get_json()['packet']
        prediction_before=client.get('/api/matches/123633/at/944').get_json()['live']
        preview=client.get('/api/matches/123633/ring?t=944&observer=1').get_json()
        self.assertTrue(preview['observer_preview'])
        self.assertEqual(preview['target']['stage'],3)
        self.assertEqual(preview['following_target']['stage'],4)
        after=client.get('/api/matches/123633/coach/packet?t=944&team=20').get_json()['packet']
        prediction_after=client.get('/api/matches/123633/at/944').get_json()['live']
        self.assertEqual(before,after)
        self.assertEqual(prediction_before,prediction_after)
        blob=json.dumps(after)
        self.assertNotIn('observer_preview',blob)
        self.assertNotIn('following_target',blob)
