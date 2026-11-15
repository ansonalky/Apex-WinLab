/* Run with node tests/timeline_regression.cjs. No server or network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/timeline.js', 'utf8');
function harness() {
  const listeners = {}, documentListeners = {}, pending = [], sought = [];
  const head = {style: {}, setAttribute(k,v) { this[k] = v; }};
  const tip = {style: {}, hidden: true, innerHTML: ''};
  const root = {hidden: true, innerHTML: '',
    addEventListener(k, fn) { listeners[k] = fn; },
    querySelector(s) { return s === '[data-playhead]' ? head : s === '[data-tip]' ? tip : null; },
    querySelectorAll() { return []; }, contains(target) { return target === this; }};
  const context = {window: {}, document: {activeElement: null,
    querySelector() {return root;}, addEventListener(k,fn) {documentListeners[k] = fn;}},
    AbortController, fetch(url, options) {return new Promise((resolve, reject) => pending.push({url, options, resolve, reject}));}};
  vm.runInNewContext(source, context);
  const api = context.window.EventTimeline;
  const sync = (gameId=1, team=20, elapsed=10) => api.sync({gameId, team, elapsed, duration:1200, seek:t=>sought.push(t)});
  const finish = (i, markers=[], rings=[]) => pending[i].resolve({ok:true, json: async()=>({available:true, markers, rings})});
  const target = (selector, value) => ({closest(s) {return s===selector ? value : null;}});
  return {api, sync, finish, pending, root, head, tip, sought, listeners, documentListeners, target};
}
(async () => {
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

  const d=fs.readFileSync('static/dashboard.js','utf8');
  assert.match(d,/async function loadMatchBody\(gameId\) \{\s*if \(window.EventTimeline\) window.EventTimeline.reset\(\)/);
  assert.match(d,/function applySquadSelection\(\) \{\s*if \(window.EventTimeline\) window.EventTimeline.reset\(\)/);
  console.log('Timeline regressions passed: selection/reset races, late responses, exact seeking, overlap picker, keyboard, dismissal, ring markers.');
})().catch(error=>{console.error(error); process.exitCode=1;});
