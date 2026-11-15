/* Run with node tests/ring_renderer_regression.cjs. No network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const renderer = {window:{}};
vm.runInNewContext(fs.readFileSync('static/map_view.js','utf8'),renderer);
function context() {
  const paths=[],stack=[];
  return {paths, fillStyle:null,strokeStyle:null,dash:[],
    save(){stack.push({fillStyle:this.fillStyle,strokeStyle:this.strokeStyle,dash:this.dash});},
    restore(){Object.assign(this,stack.pop());},
    beginPath(){this.path=[];},rect(...a){this.path.push(['rect',...a]);},arc(...a){this.path.push(['arc',...a]);},
    fill(rule){paths.push({type:'fill',style:this.fillStyle,rule,path:this.path});},
    setLineDash(dash){this.dash=dash;},
    stroke(){paths.push({type:'stroke',dash:this.dash,path:this.path});}};
}
for (const state of ['closing','waiting']) for (const zoom of [1,2,4]) {
  const ctx=context();
  renderer.drawRing(ctx,{state,observer_preview:true,location:{x:100,y:0,r:500},target:{x:200,y:0,r:100},following_target:{x:220,y:0,r:20}},
    (x,y)=>[30+x*zoom,40-y*zoom],r=>r*zoom,{mapLeft:30,mapTop:40,mapW:400*zoom,mapH:400*zoom});
  assert.equal(ctx.paths.length,3);
  const [orange,gray,dashed]=ctx.paths;
  assert.equal(orange.rule,'evenodd');assert.equal(orange.path[0][0],'rect');
  assert.equal(gray.type,'fill');assert.equal(gray.style,'rgba(205, 205, 205, 0.12)');
  assert.equal(gray.path.length,1,'gray tint fills a disc, not a band');
  assert.deepEqual(gray.path[0].slice(0,4),['arc',30+200*zoom,40,100*zoom]);
  assert.equal(dashed.type,'stroke');assert.equal(dashed.dash.join(','),'6,5');
  assert.deepEqual(dashed.path[0].slice(0,4),['arc',30+220*zoom,40,20*zoom]);
  assert.deepEqual(ctx.dash,[],'dashes cannot leak into player drawing');
}
for (const invalid of [null,{x:0,y:0,r:-1},{x:NaN,y:0,r:10},{x:0,y:0,r:Infinity}]) {
  const ctx=context();renderer.drawRing(ctx,{location:invalid,target:invalid,following_target:invalid},
    (x,y)=>[x,y],r=>r,{mapLeft:0,mapTop:0,mapW:400,mapH:400});
  assert.equal(ctx.paths.length,0);
}
const ctx=context();renderer.drawRing(ctx,{location:{x:0,y:0,r:500},target:{x:0,y:0,r:100},following_target:null},
  (x,y)=>[x,y],r=>r,{mapLeft:0,mapTop:0,mapW:400,mapH:400});
assert.equal(ctx.paths.length,2,'unknown following circle has no duplicate dashed fallback');
// The normal map: the next ring is a thin solid white outline; no gray disc and no dashed circle.
for (const zoom of [1,2,4]) {
  const ctx=context();
  renderer.drawRing(ctx,{state:'closing',location:{x:100,y:0,r:500},target:{x:200,y:0,r:100},following_target:{x:220,y:0,r:20}},
    (x,y)=>[30+x*zoom,40-y*zoom],r=>r*zoom,{mapLeft:30,mapTop:40,mapW:400*zoom,mapH:400*zoom});
  assert.equal(ctx.paths.length,2,'orange exterior and one white outline only');
  const [orange,white]=ctx.paths;
  assert.equal(orange.rule,'evenodd');
  assert.equal(white.type,'stroke');assert.equal(white.dash.join(','),'','solid, not dashed');
  assert.deepEqual(white.path[0].slice(0,4),['arc',30+200*zoom,40,100*zoom]);
}
console.log('Ring renderer passed: orange exterior, white next-ring outline on the normal map, gray disc and dashed destination in the observer preview, zoom/pan alignment and invalid geometry.');
