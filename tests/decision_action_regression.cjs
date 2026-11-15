const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/decision.js','utf8');
const code=source.slice(source.indexOf('  function reviewRequest('),source.indexOf('  box && box.addEventListener'));
const requests=[];
const ctx={running:false,state:null,result:null,token:0,LEAD_S:10,context:()=>ctx.now,now:{game:888,team:'7',t:190,start:100,squadName:'Squad',squads:[]},
 box:{scrollIntoView(){}},window:{UI:{teamIndex(){return {};}}},render(){},redraw(){},readTime:()=>ctx.state.decisionElapsed,
 async run(request){ctx.running=true;requests.push(request);await Promise.resolve();ctx.running=false;}};
vm.createContext(ctx);vm.runInContext(code,ctx);
(async()=>{
 const episode={squad_id:7,episode_start_elapsed:100,episode_end_elapsed:140,start_t:195,episode_start_t:200,prediction_kind:'final'};
 assert.equal(ctx.open(episode),90);
 const expected={game:888,team:'7',t:190};
 const first=ctx.start(expected),duplicate=ctx.start(expected);
 assert.equal(await duplicate,false);assert.equal(await first,true);assert.equal(requests.length,1);
 assert.equal(requests[0].decision_t,190);assert.equal(requests[0].episode_start_t,200);assert.equal(requests[0].kind,'final');
 ctx.now.team='8';assert.equal(await ctx.start(expected),false);assert.equal(requests.length,1);
 ctx.now.team='7';ctx.now.t=191;assert.equal(await ctx.start(expected),false);
 assert.equal(ctx.open({...episode,episode_start_elapsed:null}),undefined);
 assert.equal(ctx.open({...episode,squad_id:8}),undefined);
 console.log('Decision action passed: detector lead time, existing payload, duplicate guard and stale context refusal.');
})().catch(e=>{console.error(e);process.exitCode=1;});
