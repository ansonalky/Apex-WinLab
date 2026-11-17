const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const pending=[],box={innerHTML:''};
 const ctx={window:{UI:{mmss:s=>String(s)}},document:{querySelector:()=>box},
  fetch(url){return new Promise(resolve=>pending.push({url,resolve}));}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/tactical.js','utf8'),ctx);
 const first=ctx.window.Tactical.load(1,100,20,[]);ctx.window.Tactical.reset();
 const second=ctx.window.Tactical.load(1,200,21,[]);
 pending[1].resolve({ok:true,json:async()=>({t:200,team:'21',available:false,reason:'New selection'})});
 const result=await second;assert.equal(result.t,200);assert.equal(result.team,'21');
 pending[0].resolve({ok:true,json:async()=>({t:100,team:'20',available:false,reason:'Old selection'})});await first;
 assert.match(box.innerHTML,/New selection/);assert.doesNotMatch(box.innerHTML,/Old selection/);
 console.log('Tactical synchronization passed: awaitable response and stale-selection invalidation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
