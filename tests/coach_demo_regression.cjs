/* Run with node tests/coach_demo_regression.cjs. Provider calls are mocked. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const posts=[],timers=new Set(),listeners={};let stops=0,starts=0,resolveReview;
 const box={innerHTML:'',setAttribute(){},addEventListener(k,f){listeners[k]=f;},querySelector(){return null;}};
 const details={...box},button={disabled:false,addEventListener(){}};
 let now={game:123633,team:'20',t:1196,elapsed:1127,squadName:'EQT',squads:[]};
 const ctx={window:{UI:{mmss:t=>`${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`,teamTag:s=>s,teamIndex:()=>()=>''}},
  document:{querySelector:s=>({'#coach':box,'#coach-details':details,'#coach-run':button}[s])},
  AbortController,console,setTimeout(){const id=timers.size+1;timers.add(id);return id;},clearTimeout(id){timers.delete(id);},
  ReviewStatus:{loadingMarkup:()=>'<span>Reviewing…</span>',startClock(){starts++;return ()=>stops++;},announce(){},completionAnnouncement(){return'';},classifyFailure:()=>({status:'error',kind:'network',error:'Network error'})},
  fetch(url,options){
    if(!options)return Promise.resolve({json:async()=>({configured:true})});
    posts.push({url,options});return new Promise(resolve=>resolveReview=resolve);
  }};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/coach.js','utf8'),ctx);
 ctx.window.Coach.init({context:()=>now,seek:()=>{throw new Error('Review must not seek');}});
 const first=ctx.window.Coach.start({game:123633,team:'20',t:1196});
 assert.equal(await ctx.window.Coach.start({game:123633,team:'20',t:1196}),false);
 assert.equal(posts.length,1,'duplicate clicks create at most one POST');
 assert.equal(posts[0].url,'/api/matches/123633/coach');
 assert.deepEqual(JSON.parse(posts[0].options.body),{t:1196,team:'20'});
 resolveReview({json:async()=>({status:'error',kind:'timeout',error:'Timed out',retryable:true})});await first;
 assert.equal(starts,1);assert.equal(stops,1);assert.equal(timers.size,0);
 assert.match(box.innerHTML,/Retry 18:47/);assert.match(box.innerHTML,/No review/);
 now={...now,t:1200};
 assert.equal(await ctx.window.Coach.start({game:123633,team:'20',t:1196}),false,'wrong displayed moment cannot start demo advice');
 assert.equal(posts.length,1);assert.equal(button.disabled,false);
 console.log('Coach demo passed: exact context, one POST, timeout/Retry state and loading timer cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
