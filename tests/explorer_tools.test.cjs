const {test}=require('node:test');
const assert=require('node:assert/strict');
const {normalize,navigationStore,matchingEntries}=require('../explorer_tools.js');

test('navigation persistence contains only bounded identifiers, never source text or titles',()=>{
  const values=new Map(),storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};
  const nav=navigationStore(storage);nav.visit('s1');nav.visit('s2');nav.visit('s1');nav.toggle('s1');nav.remember('s1','m2');
  assert.deepEqual(navigationStore(storage).snapshot(),{version:1,favorites:['s1'],recent:['s1','s2'],positions:[{session:'s1',message:'m2'}]});
  const safe=normalize({favorites:['s1','s1','<script>'],recent:['s1'],positions:[{session:'s1',message:'m2',content:'PRIVATE RAW',title:'PRIVATE TITLE'},{session:'bad?x',message:'m3'}],raw:'PRIVATE'});
  assert.equal(JSON.stringify(safe).includes('PRIVATE'),false);assert.deepEqual(safe.favorites,['s1']);
  nav.toggle('s1');assert.deepEqual(nav.snapshot().favorites,[]);
});
test('malformed or blocked browser storage does not break reading',()=>{
  const nav=navigationStore({getItem:()=>'{broken',setItem:()=>{throw new Error('blocked');}});
  nav.visit('s');nav.remember('s','m');assert.equal(nav.position('s'),'m');
  assert.deepEqual(navigationStore({getItem:()=>{throw new Error('blocked');}}).snapshot().recent,[]);
});
test('recent sessions and reading positions remain unique and bounded',()=>{
  const nav=navigationStore();for(let i=0;i<250;i++){nav.visit('s'+i);nav.remember('s'+i,'m'+i);nav.toggle('s'+i);}
  assert.equal(nav.snapshot().recent.length,20);assert.equal(nav.snapshot().positions.length,100);assert.equal(nav.snapshot().favorites.length,200);
});
const entry=(source,message)=>({nodeId:'merged',source:{session_id:'s1',source_node:source},anchor:{messageId:message},answers:[{source:{source_node:'a-'+source},anchor:{messageId:'answer-'+source}}],answerEvidence:[{messageId:'quote-'+source}]});
test('search maps only rechecked message identities or source-item references, never similar words',()=>{
  const first=entry('q1','m1'),second=entry('q2','m2'),groups=[{sessionId:'s1',entries:[first,second]}],before=JSON.stringify(groups);
  assert.deepEqual(matchingEntries(groups,{sessionId:'s1',id:'unknown',snippet:'m1 q1'}),[]);
  assert.deepEqual(matchingEntries(groups,{sessionId:'other',id:'m1',analysisNodeIds:['q1']}),[]);
  assert.deepEqual(matchingEntries(groups,{sessionId:'s1',id:'m1'}),[first]);
  assert.deepEqual(matchingEntries(groups,{sessionId:'s1',id:'answer-q2'}),[second]);
  assert.deepEqual(matchingEntries(groups,{sessionId:'s1',id:'quote-q1'}),[first]);
  assert.deepEqual(matchingEntries(groups,{sessionId:'s1',id:'secondary-evidence',analysisNodeIds:['q2']}),[second]);
  assert.equal(JSON.stringify(groups),before);
});
test('ambiguous search evidence returns all exact occurrences for explicit choice',()=>{
  const one=entry('q1','same'),two=entry('q2','same');
  assert.deepEqual(matchingEntries([{sessionId:'s1',entries:[one,two]}],{sessionId:'s1',id:'same'}),[one,two]);
});
