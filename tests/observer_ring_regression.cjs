/* Run with node tests/observer_ring_regression.cjs. No server needed. */
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
(async()=>{
  const handlers={},pending=[];
  const toggle={checked:false,addEventListener(name,fn){handlers[name]=fn;}};
  const badge={hidden:true},status={textContent:''};
  const context={window:{},AbortController,document:{querySelector(s){return {'#observer-ring':toggle,'#observer-ring-badge':badge,'#observer-ring-status':status}[s];}},
    fetch(url,options){return new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}));}};
  vm.runInNewContext(fs.readFileSync('static/observer_ring.js','utf8'),context);
  const api=context.window.ObserverRing, causal={state:'waiting',target:null}, redraws=[];
  const view=(gameId=1,t=20)=>api.view({gameId,t,causal,redraw:()=>redraws.push(1)});
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const finish=(i,t)=>pending[i].resolve({ok:true,json:async()=>({t,observer_preview:true,target:{stage:2},location:{x:0,y:0,r:100}})});
  assert.equal(view(),causal);assert.equal(pending.length,0,'default must not request observer schedule');
  toggle.checked=true;handlers.change();assert.equal(badge.hidden,false);
  assert.equal(view(),causal,'show causal rings until observer response is ready');
  assert.match(pending[0].url,/\/ring\?t=20&observer=1$/);
  assert.equal(view(1,21),causal);assert.equal(pending[0].options.signal.aborted,true);
  finish(1,21);await flush();assert.equal(view(1,21).observer_preview,true);
  finish(0,20);await flush();assert.equal(view(1,21).t,21,'late response cannot restore older time');
  view(2,21);assert.equal(pending.length,3,'same second in a different game requires a separate preview');
  toggle.checked=false;handlers.change();assert.equal(badge.hidden,true);
  assert.equal(view(2,21),causal,'turning off removes future geometry immediately');
  finish(2,21);await flush();assert.equal(view(2,21),causal);
  toggle.checked=true;handlers.change();view(2,22);finish(3,999);await flush();
  assert.equal(view(2,22),causal,'timestamp mismatch cannot enter the renderer');
  assert.match(status.textContent,/unavailable/);
  api.reset();assert.equal(status.textContent,'');
  const page=fs.readFileSync('templates/index.html','utf8');
  assert.match(page,/Shows recorded future rings; players may not have known them\./);
  assert.match(page,/>Observer preview<\/span>/);
  assert.doesNotMatch(page,/id="observer-ring"[^>]*checked/);
  assert.deepEqual(Object.keys(causal),['state','target'],'observer code cannot mutate the causal frame');
  console.log('Observer frontend passed: default off, opt-in display endpoint, toggle/time/game races, exact timestamp and no causal mutation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
