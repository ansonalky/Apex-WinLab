/* Run with node tests/start_demo_regression.cjs. All provider requests mocked. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/dashboard.js','utf8');
const demo=source.slice(source.indexOf('// Explicit review action'),source.indexOf('async function loadMatch('));
const seekCode=source.slice(source.indexOf('function seek('),source.indexOf('// Explanatory panel'));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function harness(other=false) {
 const button={disabled:false},manual={hidden:true},at=[],reviews=[],paint=[],statuses=[];
 const causal=t=>({t,state:'closing_target_unknown',location:null,target:null,following_target:null});
 const ctx={AbortController,
   summary:{game_id:other?999:123633,playing_start:69,squads:[]},
   frame:{t:100,elapsed:31},currentRing:{state:'waiting',location:{x:0,y:0,r:500}},
   inFlight:null,pendingSeek:null,requestToken:0,activeLoad:null,tacticalTimer:null,
   matchSelect:{value:other?'999':'123633',options:[{value:'999'},{value:'123633'}]},
   squadSelect:{value:'20',options:[{value:'20'},{value:'21'}]},timeInput:{value:'31',max:'1200',disabled:false},
   clock:{textContent:''},shown:{textContent:''},console,hold:false,
   document:{querySelector(id){return id==='#key-moment-manual'?manual:button;}},clearTimeout(){},showStartHint(){},primaryKind(){return 'final';},async fetchHighlights(){return {ok:true,body:{available:true,down:ctx.down}};},
   setStartStatus(s){statuses.push(s);},
   replayStart(){return ctx.summary.playing_start;},selectedTeam(){return ctx.squadSelect.value;},
   mmss(t){return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`;},
   getJSON(url){
     if(url.includes('/at/')){const t=Number(url.split('/').pop());const result={t,elapsed:t-69,ring:causal(t)};
       if(ctx.hold){const d=deferred();at.push({t,...d});return d.promise.then(()=>result);}return Promise.resolve(result);}
     assert.match(url,/\/ring\?t=1196$/);return ctx.ringWait ? ctx.ringWait.promise : Promise.resolve(causal(1196));
   },
   safely(fn){return fn();},renderOutcome(){},draw(){paint.push({t:ctx.frame.t,ring:ctx.currentRing});},renderRoster(){},renderPredictions(){},
   syncTimeline(){return ctx.timelineWait ? ctx.timelineWait.promise : Promise.resolve();},loadTactical(){},
   down:[{change_pp:-5,episode_start_elapsed:200},{change_pp:-30,episode_start_elapsed:1137}],
   window:{Tactical:{reset(){},async load(game,t,team){return {t,team};}},Coach:{update(){}},DecisionReview:{update(){},open(item){return item.episode_start_elapsed-10;},async start(expected){
      assert.equal(ctx.frame.t,1196);assert.equal(ctx.frame.elapsed,1127);assert.equal(ctx.squadSelect.value,'20');
      assert.equal(ctx.currentRing.state,'closing_target_unknown');assert.equal(ctx.currentRing.location,null);
      reviews.push(expected);return true;
   }}}};
 vm.createContext(ctx);vm.runInContext(demo+'\n'+seekCode,ctx);
 return {ctx,button,manual,at,reviews,paint,statuses};
}
(async()=>{
 for(const other of [false,true]){
   const h=harness(other);const first=h.ctx.reviewKeyMoment(),second=h.ctx.reviewKeyMoment();
   assert.equal(first,second,'repeated demo clicks share one sequence');await first;
   assert.equal(h.reviews.length,1);assert.equal(h.ctx.matchSelect.value,other?'999':'123633');assert.equal(h.ctx.timeInput.value,'1127');
   assert.equal(h.button.disabled,false);assert.equal(h.paint.at(-1).ring.location,null,'earlier circle is replaced by unavailable final ring');
 }
 const h=harness();h.ctx.hold=true;h.ctx.loadFrame(500);const demoRun=h.ctx.reviewKeyMoment();await flush();
 assert.equal(h.at.length,1);assert.equal(h.reviews.length,0);
 h.at[0].resolve();await flush();assert.equal(h.at[1].t,1196);assert.equal(h.reviews.length,0);
 h.at[1].resolve();await demoRun;assert.equal(h.reviews.length,1,'demo awaits the full queued seek');
 for(const cancel of ['time','squad','match']){
   const h=harness();h.ctx.ringWait=deferred();const run=h.ctx.reviewKeyMoment();await flush();
   if(cancel==='time')h.ctx.timeInput.value='100';
   if(cancel==='squad')h.ctx.squadSelect.value='21';
   if(cancel==='match')h.ctx.matchSelect.value='999';
   h.ctx.cancelKeyMoment();h.ctx.ringWait.resolve({t:1196,state:'closing_target_unknown',location:null});await run;
   assert.equal(h.reviews.length,0,`${cancel} cancels the demo before the review`);
 }
 const t=harness();t.ctx.timelineWait=deferred();const timelineRun=t.ctx.reviewKeyMoment();await flush();
 assert.equal(t.reviews.length,0,'timeline must synchronize before review');t.ctx.timelineWait.resolve();await timelineRun;assert.equal(t.reviews.length,1);
 const init=harness();init.ctx.timeInput.disabled=true;init.ctx.activeLoad=deferred().promise;
 const initWait=deferred();init.ctx.activeLoad=initWait.promise;const initRun=init.ctx.reviewKeyMoment();await flush();
 assert.equal(init.reviews.length,0);init.ctx.timeInput.disabled=false;initWait.resolve();await initRun;assert.equal(init.reviews.length,1);
 const missing=harness();missing.ctx.down=[];await missing.ctx.reviewKeyMoment();
 assert.equal(missing.reviews.length,0);assert.equal(missing.manual.hidden,false);
 assert.equal(missing.statuses.at(-1),'No review moment found for this squad');
 assert.equal(missing.ctx.timeInput.value,'31');
 missing.ctx.down=[{change_pp:-30,episode_start_elapsed:1137}];await missing.ctx.reviewKeyMoment();
 assert.equal(missing.reviews.length,1,'no-highlight result does not lock future clicks');
 const late=harness();const highlights=deferred();late.ctx.fetchHighlights=()=>highlights.promise;
 const lateRun=late.ctx.reviewKeyMoment();await flush();late.ctx.squadSelect.value='21';late.ctx.cancelKeyMoment();
 highlights.resolve({ok:true,body:{available:true,down:late.ctx.down}});await lateRun;
 assert.equal(late.reviews.length,0);assert.equal(late.ctx.timeInput.value,'31','late highlights cannot navigate');
 console.log('Key-moment action passed: preserved matches, largest drop, no-highlight fallback, late highlights, queued seek, full frame/ring/timeline synchronization, cancellation, duplicate clicks and unavailable final ring.');
})().catch(error=>{console.error(error);process.exitCode=1;});
