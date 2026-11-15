/* Run with node tests/ring_renderer_regression.cjs. No network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const renderer = {window:{}};
vm.runInNewContext(fs.readFileSync('static/map_view.js','utf8'),renderer);
for (const state of ['closing','waiting']) {
  for (const zoom of [1,2,4]) {
    const arcs=[], calls=[];
    const ctx={save(){},restore(){},beginPath(){},rect(){},arc(...args){arcs.push(args);},
      fill(rule){calls.push(rule);},stroke(){calls.push('stroke');}};
    const project=(x,y)=>[30+x*zoom,40-y*zoom];
    const radius=r=>r*zoom;
    renderer.drawRing(ctx,{state,location:{x:100,y:0,r:500},target:{x:200,y:0,r:100}},
      project,radius,{mapLeft:30,mapTop:40,mapW:400*zoom,mapH:400*zoom});
    assert.deepEqual(arcs.map(a=>a.slice(0,3)),[[30+100*zoom,40,500*zoom],[30+200*zoom,40,100*zoom]]);
    assert.deepEqual(calls,['evenodd','stroke'],'waiting and closing share the same projection and radius');
  }
}
let strokes=0;
const ctx={save(){},restore(){},beginPath(){},rect(){},arc(){},fill(){},stroke(){strokes++;}};
renderer.drawRing(ctx,{state:'waiting',location:{x:100,y:0,r:500},target:null},
  (x,y)=>[x,y],r=>r,{mapLeft:0,mapTop:0,mapW:400,mapH:400});
assert.equal(strokes,0,'unknown next target is not invented');
console.log('Ring renderer regressions passed: phase-independent targets, shared pan/zoom transform, unknown-target omission.');
