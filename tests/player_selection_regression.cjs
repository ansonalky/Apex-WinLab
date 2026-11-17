/* Run with node tests/player_selection_regression.cjs. No server or AI calls. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync('static/dashboard.js','utf8');
const selection=script.slice(script.indexOf('function selectSquad('),script.indexOf('window.ApexMap.onSelect ='));
const calls=[];const listeners={};
const ctx={selectedPlayerId:null, selectionScroll:null,frame:{t:100,players:[
  {id:'one',name:'Same name',teamId:20,x:0,y:0},
  {id:'two',name:'Same name',teamId:20,x:null,y:null},
  {id:'three',name:'Other',teamId:21,x:1,y:1}]},
 squadSelect:{value:'20',options:[{value:'20'},{value:'21'}]},
 selectedTeam(){return this.squadSelect.value;},
 window:{scrollX:0,scrollY:0,scrollTo(){}},requestAnimationFrame(fn){fn();},
 draw(){calls.push('draw');},renderRoster(){calls.push('roster');},
 applySquadSelection(options){assert.equal(options.preservePlayer,true);calls.push('squad');},
 roster:{addEventListener(k,f){listeners[k]=f;},querySelectorAll(){return[];}},document:{activeElement:null}};
// Avoid a `this` dependency in the test's selectedTeam stub.
ctx.selectedTeam=()=>ctx.squadSelect.value;
vm.createContext(ctx);vm.runInContext(selection,ctx);
ctx.selectSquad(20,'one');assert.equal(ctx.selectedPlayerId,'one');
ctx.selectSquad(20,'two');assert.equal(ctx.selectedPlayerId,'two','same name and no position still select by ID');
ctx.frame={...ctx.frame,t:101,players:ctx.frame.players.map(p=>({...p,name:'Renamed'}))};
assert.equal(ctx.selectedPlayerId,'two','seeking or renaming does not clear ID');
ctx.selectSquad(21,'three');assert.equal(ctx.squadSelect.value,'21');assert.equal(ctx.selectedPlayerId,'three');
ctx.selectSquad(21);assert.equal(ctx.selectedPlayerId,null,'team table clears individual even for same team');
ctx.selectSquad(20,'three');assert.equal(ctx.selectedPlayerId,null,'wrong-team ID cannot be selected');
ctx.selectSquad(20,'one');
const row={dataset:{playerId:'two'},contains(){return false;}};
listeners.keydown({key:' ',preventDefault(){calls.push('preventDefault');},target:{closest(){return row;}}});
assert.equal(ctx.selectedPlayerId,'two','Space supports keyboard roster selection');
listeners.click({target:{closest(){return {dataset:{playerId:'one'},contains(){return false;}};}}});
assert.equal(ctx.selectedPlayerId,'one','roster click shares map selection path');
assert.equal(ctx.frame.t,101,'selection cannot seek');
assert.doesNotMatch(selection,/fetch\(|Coach\.|DecisionReview\.|seek\(/,'player selection cannot request AI or change replay');
assert.match(script,/function applySquadSelection\(options = \{\}\) \{[\s\S]*?if \(!options.preservePlayer\) selectedPlayerId = null/);
assert.match(script,/async function loadMatchBody\(gameId\) \{[\s\S]*?selectedPlayerId = null/);
assert.match(script,/role="button" tabindex=/);assert.match(script,/aria-pressed="\$\{selected\}"/);

const map={window:{},console};vm.createContext(map);
vm.runInContext(fs.readFileSync('static/map_view.js','utf8'),map);
vm.runInContext(`hits=[{x:10,y:10,teamId:'20',playerId:'one',playerName:'Same'},
 {x:11,y:10,teamId:'20',playerId:'two',playerName:'Same'},
 {x:100,y:100,teamId:'21',playerId:'three',playerName:'Other'}]`,map);
assert.equal(map.playersAt(10,10).length,2,'overlap choices preserve teammates as individual players');
assert.equal(map.playersAt(10,10)[0].playerId,'one');
assert.equal(map.playersAt(10,10)[1].playerId,'two');
// Current position validity: null/undefined/NaN players never enter hit-testing.
const source=fs.readFileSync('static/map_view.js','utf8');
assert.match(source,/Number.isFinite\(player.x\) && Number.isFinite\(player.y\)/);
assert.match(source,/onSelect\(found\[0\].teamId, found\[0\].playerId\)/);
assert.match(source,/onSelect\(option.teamId, option.playerId\)/);
assert.match(source,/option.playerName/);
console.log('Player selection passed: stable IDs, duplicate names, missing positions, team clearing, keyboard roster, player overlap and no seek/AI calls.');
