"""Map targets advance only when the causal replay has revealed the next stage."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from overstat_patch import apply_frame, initial_state, frame_entries
from ring_features import live_entries, ring_at
from ring_overlay import overlay_geometry
from ring_label import ring_label
from tactical import TacticalIndex

FIRST = dict(stage=0, start=10, end=20, startCenter=dict(x=0,y=0),
             endCenter=dict(x=100,y=0), startSize=1000, endSize=500)
NEXT = dict(stage=1, start=30, end=40, startCenter=dict(x=100,y=0),
            endCenter=dict(x=200,y=0), startSize=500, endSize=100)
FINAL = dict(stage=2, start=45, startCenter=dict(x=200,y=0), startSize=100, endSize=1)


def replay(reveal=19):
    planned = copy.deepcopy(NEXT)
    planned['endCenter'] = dict(x=9999,y=9999)
    return [dict(t=0,d=dict(state=['Playing'],players=[{}],rings=[[FIRST,planned,FINAL]])),
            dict(t=5,d=dict(rings={'_t':'a','0':[FIRST]})),
            dict(t=reveal,d=dict(rings={'_t':'a','1':[NEXT]})),
            dict(t=45,d=dict(rings={'_t':'a','2':[FINAL]})), dict(t=50)]


class Transition(unittest.TestCase):
    def build(self, reveal=19):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raw = Path(tmp.name)/'replay.json'
        raw.write_text(json.dumps(dict(series=replay(reveal))))
        with patch('tactical._record',return_value={"game_id":1}), patch('tactical._raw_path',return_value=raw):
            index = TacticalIndex(Path(tmp.name))
            index.game(1)
        return index

    def test_before_at_and_after_closure_and_waiting(self):
        index=self.build()
        for t in (18,19):
            ring=index.ring_overlay_at(1,t)
            self.assertEqual(ring['target']['stage'],0)
            self.assertEqual(ring['state'],'closing')
            self.assertEqual(ring['location']['r'],1000-(t-10)*50)
        for t in (20,21,25,29):
            ring=index.ring_overlay_at(1,t)
            self.assertEqual(ring['state'],'waiting')
            self.assertEqual(ring['label'],'Ring 1 closed · ring 2 next')
            self.assertEqual(ring['target'],dict(x=200.,y=0.,r=100.,stage=1))
            self.assertEqual(ring['location'],dict(x=100.,y=0.,r=500.))
        ring=index.ring_overlay_at(1,35)
        self.assertEqual(ring['location'],dict(x=150.,y=0.,r=300.))
        self.assertEqual(ring['target']['stage'],1)
        self.assertIsNone(index.ring_overlay_at(1,40)['target'])
        self.assertIsNone(index.ring_overlay_at(1,45)['location'])
        self.assertIsNone(index.ring_overlay_at(1,45)['target'])

    def test_reveal_on_closure_applies_without_a_blank_second(self):
        index=self.build(reveal=20)
        self.assertEqual(index.ring_overlay_at(1,19)['target']['stage'],0)
        self.assertEqual(index.ring_overlay_at(1,20)['target']['stage'],1)
        self.assertEqual(index.ring_overlay_at(1,21)['target']['stage'],1)

    def test_unknown_next_is_not_read_from_plan_or_future(self):
        index=self.build(reveal=25)
        for t in (0,4,20,21,24):
            self.assertIsNone(index.ring_overlay_at(1,t)['target'])
        self.assertEqual(index.ring_overlay_at(1,25)['target']['x'],200)
        self.assertNotEqual(index.ring_overlay_at(1,25)['target']['x'],9999)

    def test_countdown_before_first_closing_uses_revealed_geometry(self):
        ring,target,convention=overlay_geometry([FIRST],5)
        self.assertEqual(ring['radius'],1000)
        self.assertEqual(target['stage'],0)
        self.assertEqual(ring_label(ring['phase'],ring['stage'],convention)['label'],'Waiting · ring 1 next')

    def test_backward_seeking_and_coach_ring_reader_stay_unchanged(self):
        index=self.build()
        times=(4,5,18,19,20,21,25,30,35,40,45)
        forward=[index.ring_overlay_at(1,t) for t in times]
        self.assertEqual(forward,[index.ring_overlay_at(1,t) for t in reversed(times)][::-1])
        state,observed=initial_state(),{}
        for frame in frame_entries(replay()):
            state,observed,_=apply_frame(state,frame.get('d'),frame.get('f') or [],observed,frame['t'])
            self.assertEqual(index.game(1)['seconds'][frame['t']]['ring'],
                             ring_at(live_entries(state,observed),frame['t']))

    def test_missing_null_invalid_next_targets_and_no_skipped_stages(self):
        for field in ('start','end','startCenter','endCenter','startSize','endSize'):
            for value in (None,True,'invalid',float('nan'),float('inf')):
                bad=copy.deepcopy(NEXT); bad[field]=value
                with self.subTest(field=field,value=value):
                    ring,target,_=overlay_geometry([FIRST,bad],21)
                    self.assertTrue(ring['known'])
                    self.assertIsNone(target)
        later=copy.deepcopy(NEXT); later['stage']=2
        self.assertIsNone(overlay_geometry([FIRST,later],21)[1])
