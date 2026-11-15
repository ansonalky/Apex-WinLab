/* Run with node tests/timeline_regression.cjs. No server or network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/timeline.js', 'utf8');
function harness() {
  const listeners = {}, documentListeners = {}, pending = [], sought = [];
  const head = {style: {}, setAttribute(k,v) { this[k] = v; }};
  const tip = {style: {}, hidden: true, innerHTML: ''};
  const captured = new Set();
  const track = {getBoundingClientRect() {return {left:10,width:600};},
    setPointerCapture(id) {captured.add(id);}, hasPointerCapture(id) {return captured.has(id);},
    releasePointerCapture(id) {captured.delete(id);}};
  const root = {hidden: true, innerHTML: '',
    addEventListener(k, fn) { listeners[k] = fn; },
    querySelector(s) { return s === '[data-playhead]' ? head : s === '[data-tip]' ? tip : s === '[data-track]' ? track : null; },
    querySelectorAll() { return []; }, contains(target) { return target === this; }};
  const context = {window: {}, document: {activeElement: null,
    querySelector() {return root;}, addEventListener(k,fn) {documentListeners[k] = fn;}},
    AbortController, fetch(url, options) {return new Promise((resolve, reject) => pending.push({url, options, resolve, reject}));}};
  vm.runInNewContext(source, context);
  const api = context.window.EventTimeline;
  const sync = (gameId=1, team=20, elapsed=10) => api.sync({gameId, team, elapsed, duration:1200, seek:t=>sought.push(t)});
  const finish = (i, markers=[], rings=[]) => pending[i].resolve({ok:true, json: async()=>({available:true, markers, rings})});
  const target = (selector, value) => ({closest(s) {return s===selector ? value : null;}});
  return {api, sync, finish, pending, root, head, tip, track, captured, sought, listeners, documentListeners, target};
}
(async () => {
  const ready=harness();
  const loading=ready.sync();
  const alsoWaiting=ready.sync(1,20,15);
  assert.equal(loading,alsoWaiting,'same-selection sync awaits the pending timeline render');
  ready.finish(0);await alsoWaiting;
  assert.equal(ready.head['aria-valuenow'],'15');
  const h = harness();
  let a = h.sync();
  h.finish(0, [{kind:'kill',elapsed:10,text:'Old squad'}]); await a;
  assert.match(h.root.innerHTML, /Old squad/);
  let b = h.sync(1,21);
  assert.equal(h.root.innerHTML, '', 'selection clears old markers synchronously');
  assert.equal(h.root.hidden,true);
  let c = h.sync(2,21);
  assert.equal(h.pending[1].options.signal.aborted,true);
  h.finish(2,[{kind:'knock',elapsed:30,text:'Current match'}]); await c;
  h.finish(1,[{kind:'death',elapsed:40,text:'Late old response'}]); await b;
  assert.match(h.root.innerHTML,/Current match/);
  assert.doesNotMatch(h.root.innerHTML,/Late old response|Old squad/);
  const before = h.pending.length;
  await h.sync(2,21,42);
  assert.equal(h.pending.length,before,'same selection reuses payload');
  assert.equal(h.head['aria-valuenow'],'42');
  h.api.reset(); assert.equal(h.root.innerHTML,'');
  a=h.sync(); h.api.reset(); h.finish(3,[{kind:'kill',elapsed:10,text:'Late reset'}]); await a;
  assert.equal(h.root.innerHTML,'','reset also invalidates late responses');

  const p=harness(); a=p.sync();
  p.finish(0,[{kind:'kill',elapsed:1127,text:'Kill A'},{kind:'knock',elapsed:1128,text:'Knock B'}],
    [{ring_number:1,end_recorded:true,start_elapsed:50,end_elapsed:100},
     {ring_number:6,end_recorded:false,start_elapsed:1100,end_elapsed:null}]); await a;
  assert.match(p.root.innerHTML,/timeline-tick ring-start/);
  assert.match(p.root.innerHTML,/timeline-tick ring-close/);
  assert.match(p.root.innerHTML,/white playhead/);
  assert.match(p.root.innerHTML,/20:00/);
  const button={dataset:{group:'2'}};
  p.listeners.click({target:p.target('[data-group]',button)});
  assert.match(p.tip.innerHTML,/18:47 · Kill A/);
  assert.match(p.tip.innerHTML,/18:48 · Knock B/);
  assert.equal(p.sought.length,0,'overlap picker does not guess an event');
  const originalQuery = p.root.querySelector;
  p.root.querySelector = (selector) => selector.startsWith('[data-group=') ? {focus() {p.listeners.focusin({target:p.target('[data-group]',button)});}} : originalQuery(selector);
  p.listeners.click({target:p.target('[data-event]',{dataset:{event:'1'}})});
  assert.equal(p.sought.pop(),1128,'chosen event seeks exact timestamp');
  assert.equal(p.tip.hidden,true);
  p.listeners.focusin({target:p.target('[data-group]',button)});
  assert.equal(p.tip.hidden,false,'keyboard focus exposes all overlapping events');
  p.listeners.keydown({key:'Escape',target:p.target('',null),preventDefault(){}});
  assert.equal(p.tip.hidden,true);
  p.listeners.keydown({key:'ArrowRight',shiftKey:true,target:p.target('[data-playhead]',{}),preventDefault(){}});
  assert.equal(p.sought.pop(),20);
  p.listeners.keydown({key:'End',target:p.target('[data-playhead]',{}),preventDefault(){}});
  assert.equal(p.sought.pop(),1200);
  p.listeners.click({target:p.target('[data-track]',{getBoundingClientRect(){return {left:10,width:600};}}),clientX:310});
  assert.equal(p.sought.pop(),600);
  p.documentListeners.pointerdown({target:{}}); assert.equal(p.tip.hidden,true);

  for (const pointerType of ['mouse', 'touch', 'pen']) {
    const r=harness(); const ready=r.sync(); r.finish(0); await ready;
    const pointer = (x, extras={}) => ({pointerId:7, isPrimary:true, pointerType, button:0,
      clientX:x, clientY:10, target:r.target('[data-track]',r.track), ...extras});
    r.listeners.pointerdown(pointer(310));
    assert.equal(r.captured.has(7),true, 'pointer captured for release outside');
    r.listeners.pointermove(pointer(460));
    assert.equal(r.sought.at(-1),900, pointerType+' forward drag');
    await r.sync(1,20,15);
    assert.equal(r.head['aria-valuenow'],'900','late frame cannot move dragging playhead back');
    r.listeners.pointermove(pointer(160));
    assert.equal(r.sought.at(-1),300, pointerType+' backward drag');
    r.listeners.pointermove(pointer(500,{pointerId:8,isPrimary:false}));
    assert.equal(r.sought.at(-1),300,'another finger cannot change the scrub');
    r.listeners.pointerup(pointer(-50,{target:r.target('',null)}));
    assert.equal(r.sought.at(-1),0, pointerType+' release outside left clamps');
    assert.equal(r.captured.size,0,'capture released');
    const count=r.sought.length;
    r.listeners.click({detail:1,target:r.target('[data-track]',r.track),clientX:400,preventDefault(){},stopPropagation(){}});
    assert.equal(r.sought.length,count,'synthetic drag click does not seek again');
    r.listeners.pointermove(pointer(300));
    assert.equal(r.sought.length,count,'moving after release does not seek');
    r.listeners.pointerdown(pointer(310)); r.listeners.pointermove(pointer(460));
    r.listeners.pointerup(pointer(900));
    assert.equal(r.sought.at(-1),1200,pointerType+' release outside right clamps');

    r.listeners.pointerdown(pointer(310)); r.listeners.pointermove(pointer(460));
    const beforeCancel=r.sought.length;
    r.listeners.pointercancel(pointer(0));
    assert.equal(r.sought.length,beforeCancel,'cancellation does not invent a final position');
    r.listeners.pointerdown(pointer(310)); r.listeners.pointermove(pointer(160));
    r.listeners.lostpointercapture(pointer(160));
    const beforeLost=r.sought.length; r.listeners.pointermove(pointer(460));
    assert.equal(r.sought.length,beforeLost,'lost capture ends scrubbing');
    r.listeners.pointerdown(pointer(310)); r.listeners.pointermove(pointer(160));
    r.api.reset(); assert.equal(r.captured.size,0,'selection reset releases capture');
    const beforeReset=r.sought.length; r.listeners.pointerup(pointer(500));
    assert.equal(r.sought.length,beforeReset,'release cannot seek the previous selection');
  }
  // Ordinary tick taps preserve the original capture target and overlap picker.
  const taps=harness(); a=taps.sync();
  taps.finish(0,[{kind:'kill',elapsed:100,text:'A'},{kind:'knock',elapsed:101,text:'B'}]); await a;
  const tick={...taps.track,dataset:{group:'0'}};
  const tickTarget={closest(s) {return s==='[data-track]' ? taps.track :
    s==='[data-group], [data-playhead]' || s==='[data-group]' ? tick : null;}};
  for (const pointerType of ['mouse','touch']) {
    const event={pointerId:1,isPrimary:true,pointerType,button:0,clientX:60,clientY:10,target:tickTarget};
    taps.listeners.pointerdown(event); taps.listeners.pointermove({...event,clientX:62});
    taps.listeners.pointerup(event); taps.listeners.click({...event,detail:1});
    assert.match(taps.tip.innerHTML,/Choose an event/);
    assert.equal(taps.sought.length,0,'tap does not become an arbitrary track seek');
  }
  assert.match(fs.readFileSync('static/timeline.css','utf8'),/touch-action: none/);

  const d=fs.readFileSync('static/dashboard.js','utf8');
  const init=d.slice(d.indexOf('async function loadMatchBody('),d.indexOf('  const token',d.indexOf('async function loadMatchBody(')));
  assert.match(init,/window.EventTimeline.reset\(\)/);
  const selection=d.slice(d.indexOf('function applySquadSelection('),d.indexOf('  loadHighlights();',d.indexOf('function applySquadSelection(')));
  assert.match(selection,/window.EventTimeline.reset\(\)/);
  console.log('Timeline regressions passed: selection/reset races, late responses, exact seeking, overlap picker, keyboard, dismissal, ring markers, mouse/touch/pen dragging, capture, cancellation, outside release.');
})().catch(error=>{console.error(error); process.exitCode=1;});
