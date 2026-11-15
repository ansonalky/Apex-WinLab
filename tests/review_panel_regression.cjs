/* Real frontend modules, deferred fixture responses; no provider or network. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const node=()=>({innerHTML:'',textContent:'',hidden:false,disabled:false,dataset:{},listeners:{},attributes:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,f){(this.listeners[k]??=[]).push(f);},scrollIntoView(){},querySelector(){return null;}});
(async()=>{
 const output=node(),details=node(),setup=node(),pin=node(),analyze=node(),key=node(),run=node(),heading=node(),notice=node(),dd=node(),sd=node(),time={value:'01:30'};
 output.querySelector=s=>s==='#decision-time'?time:s.includes('run')?run:null;
 const tabs=['situation','decision'].map(mode=>Object.assign(node(),{dataset:{reviewMode:mode}}));
 const nodes={'#coach':output,'#coach-details':details,'#coach-run':analyze,'#start-here':key,'#review-context':heading,'#review-notice':notice,'#situation-details':sd};
 const posts=[],timers=new Map();let tid=0,clocks=0;
 const now={game:888,team:'7',t:190,elapsed:90,start:100,squadName:'Squad',squads:[]};
 const ctx={document:{querySelector:s=>nodes[s],querySelectorAll:s=>s==='[data-review-mode]'?tabs:[analyze,key,run]},window:{UI:{mmss:s=>`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`,teamIndex:()=>()=> 'Squad',teamTag:s=>s,keep:()=>'',span:()=>'',finite:Number.isFinite,pct:()=>'',pp:()=>'',text:()=>''}},AbortController,console,
 setTimeout(f){timers.set(++tid,f);return tid;},clearTimeout(id){timers.delete(id);},
 ReviewStatus:{loadingMarkup:()=>'<p>Reviewing…</p>',startClock(){clocks++;let stopped=false;return()=>{if(!stopped){clocks--;stopped=true;}};},announce(){},completionAnnouncement:()=>'',classifyFailure:()=>({status:'error',error:'Network failure',retryable:true})},
 fetch(url,options){if(!options)return Promise.resolve({json:async()=>({configured:true})});let resolve;const promise=new Promise(r=>resolve=r);posts.push({url,options,resolve});return promise;}};
 vm.createContext(ctx);for(const file of ['review_panel.js','coach.js','decision.js'])vm.runInContext(fs.readFileSync('static/'+file,'utf8'),ctx);
 const C=ctx.window.Coach,D=ctx.window.DecisionReview,P=ctx.window.ReviewPanel;
 C.init({context:()=>now,seek(){}});D.init({context:()=>now,seek(){},redraw(){}});await flush();
 const episode={squad_id:7,squad_name:'Squad',episode_start_elapsed:100,episode_end_elapsed:140,prediction_kind:'final_model_fitted',start_t:195,episode_start_t:200};
 assert.equal(D.open(episode),90);assert.equal(sd.hidden,true);assert.match(output.innerHTML,/<summary>Review details<\/summary>[\s\S]*decision-time/);assert.match(output.innerHTML,/decision at <strong>01:30/);
 const first=D.start({game:888,team:'7',t:190});assert.equal(posts.length,1);assert.match(posts[0].url,/decision-review$/);
 assert.equal(await C.start(),false);assert.equal(posts.length,1);assert.equal(analyze.disabled,true);assert.equal(key.disabled,true);assert.equal(run.disabled,true);
 assert.equal(P.select('situation'),false);assert.equal(notice.textContent,'A review is already running');
 function click(attr,value){const target={closest:s=>s===`button[data-${attr}]`?{dataset:{[attr]:value}}:null};for(const handler of output.listeners.click)handler({target});}
 click('decision','cancel');assert.equal(clocks,0);assert.equal(timers.size,0);assert.equal(analyze.disabled,false);assert.match(output.innerHTML,/billing may continue/);
 const second=C.start();assert.equal(posts.length,2);assert.match(posts[1].url,/\/coach$/);
 const loading=output.innerHTML;posts[0].resolve({json:async()=>({status:'error',error:'LATE DECISION'})});await first;assert.equal(output.innerHTML,loading,'late cancelled result cannot replace another mode');
 posts[1].resolve({json:async()=>({status:'error',error:'Fixture failure',retryable:true})});await second;assert.match(output.innerHTML,/Retry/);assert.match(heading.textContent,/Current situation · Squad at 01:30/);
 click('coach','retry');assert.equal(posts.length,3);assert.equal(await D.start({game:888,team:'7',t:190}),false);
 click('coach','cancel');assert.equal(clocks,0);assert.equal(timers.size,0);
 P.select('decision');assert.match(output.innerHTML,/billing may continue/);click('decision','retry');assert.equal(posts.length,4);
 posts[3].resolve({json:async()=>({status:'unavailable',reason:'Fixture unavailable'})});await flush();assert.match(output.innerHTML,/Fixture unavailable/);assert.equal(clocks,0);assert.equal(timers.size,0);
 posts[2].resolve({json:async()=>({status:'error',error:'LATE SITUATION'})});await flush();assert.doesNotMatch(output.innerHTML,/LATE/);
 const packet={clock:'01:30',items:[],window:{start_clock:'00:30',end_clock:'01:30'},retrospective:{items:[],from_clock:'01:31',to_clock:'02:20'}};
 const base={status:'ok',cached:true,game_id:888,team:'7',squad_name:'Squad',clock:'01:30',t:190,window:{start_t:130,start_clock:'00:30',end_clock:'01:30'},packet};
 const success=C.start();posts[4].resolve({json:async()=>({...base,analysis:{next_action:'Fixture situation advice.',factors:[],recommendations:[]}})});await success;
 assert.match(output.innerHTML,/Fixture situation advice/);assert.match(details.innerHTML,/from cache/);
 P.select('decision');const decisionSuccess=D.start({game:888,team:'7',t:190});
 posts[5].resolve({json:async()=>({...base,analysis:{next_action:'Fixture decision advice.',options:[],recommendation:{},drop_attribution:{}}})});await decisionSuccess;
 assert.match(output.innerHTML,/Fixture decision advice/);assert.match(output.innerHTML,/not used for recommendations/);
 P.select('situation');assert.match(output.innerHTML,/Fixture situation advice/);assert.doesNotMatch(output.innerHTML,/Fixture decision advice/);
 assert.equal(P.busy(),false);assert.equal(output.attributes['aria-busy'],'false');
 console.log('Unified review passed: both endpoints, cross-mode locking, disabled submissions, tab safety, cancel, late responses, Retry and timer cleanup.');
})().catch(e=>{console.error(e);process.exitCode=1;});
