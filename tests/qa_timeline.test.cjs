const {test} = require('node:test');
const assert = require('node:assert/strict');
const {build,byTopic,orderedTopics,topicsFor,scopedGroup,itemsForTopic,mindBranch,applyTopicDisplay,applySessionDisplay,sessionTitle} = require('../graph_view.js');
function fixture() {
  const provenance=(source,sid='s')=>[{session_id:sid,source_node:source}];
  const node=(id,classification='answered_question',sid='s')=>({id,title:id,project:'예시',classification,provenance:provenance(id,sid)});
  const nodes=[node('q-late'),node('q-early'),node('answer','assistant_statement'),node('unknown'),node('same-turn')];
  const graph={nodes,edges:[{id:'answer-edge',from:'answer',to:'q-early',type:'answers',sessions:['s']}]};
  const context={s:{session:{title:'가상 세션'},messages:[
    {id:'sha-late',index:12,role:'user',timestamp:''},
    {id:'sha-early',index:2,role:'user',timestamp:'2026-10-20T00:00:00Z'},
    {id:'sha-answer',index:3,role:'assistant',timestamp:'2026-10-20T00:01:00Z'}],analysis:{anchors:{
      'q-late':[{messageId:'sha-late',index:12,sourceMessageId:'m13'}],
      'q-early':[{messageId:'sha-answer',index:3,sourceMessageId:'m4'},{messageId:'sha-early',index:2,sourceMessageId:'m3'}],
      answer:[{messageId:'sha-answer',index:3,sourceMessageId:'m4'}],
      'same-turn':[{messageId:'sha-early',index:2,sourceMessageId:'m3'}],unknown:[]},
    relations:[{id:'original-edge',from:'answer',to:'q-early',type:'answers'}]}}};
  return {graph,context,node};
}
test('orders rechecked user anchors, keeps missing timestamps and ties explicit',()=>{
  const {graph,context}=fixture(), rows=build(graph,context)[0].entries;
  assert.deepEqual(rows.map(row=>row.nodeId),['q-early','same-turn','q-late','unknown']);
  assert.equal(rows[0].anchor.messageId,'sha-early');
  assert.equal(rows[0].sameMessage,true);assert.equal(rows[1].sameMessage,true);
  assert.equal(rows[2].timestamp,'');assert.equal(rows[3].anchor,null);
});
test('pairs only stored answers in the right direction; no adjacency guessing',()=>{
  const {graph,context}=fixture();
  graph.edges.push({id:'related',from:'answer',to:'q-late',type:'related',sessions:['s']},
    {id:'reversed',from:'unknown',to:'answer',type:'answers',sessions:['s']});
  const rows=build(graph,context)[0].entries;
  assert.deepEqual(rows[0].answers.map(answer=>answer.nodeId),['answer']);
  assert.deepEqual(rows[0].answers[0].edgeIds,['answer-edge']);
  assert.equal(rows.find(row=>row.nodeId==='q-late').answers.length,0);
  assert.equal(rows.find(row=>row.nodeId==='unknown').answers.length,0);
});
test('does not invent source locations for missing, assistant-only or stale anchors',()=>{
  const {graph,context}=fixture();
  context.s.analysis.anchors['q-early']=[{messageId:'sha-answer',index:3,sourceMessageId:'m4'}];
  context.s.analysis.anchors['q-late'][0].index=99;
  assert.equal(build(graph,context)[0].entries.find(row=>row.nodeId==='q-early').anchor,null);
  assert.equal(build(graph,context)[0].entries.find(row=>row.nodeId==='q-late').anchor,null);
  assert.ok(build(graph)[0].entries.every(row=>row.anchor===null));
});
test('merged questions keep occurrences and answers within their original session and source',()=>{
  const {graph,context,node}=fixture();
  graph.nodes[1].provenance.push({session_id:'t',source_node:'t-q'});
  graph.nodes.push(node('foreign-answer','assistant_statement','t'));
  graph.edges.push({id:'foreign-edge',from:'foreign-answer',to:'q-early',type:'answers',sessions:['t']});
  const groups=build(graph,context);
  const s=groups.find(group=>group.sessionId==='s').entries.find(row=>row.nodeId==='q-early');
  const t=groups.find(group=>group.sessionId==='t').entries[0];
  assert.deepEqual(s.answers.map(answer=>answer.nodeId),['answer']);
  assert.deepEqual(t.answers.map(answer=>answer.nodeId),['foreign-answer']);
  context.s.analysis.relations=[];
  assert.equal(build(graph,context).find(group=>group.sessionId==='s').entries.find(row=>row.nodeId==='q-early').answers.length,0);
});
test('deduplicates source occurrences and answer cards without losing relationship IDs',()=>{
  const {graph,context}=fixture();
  graph.nodes[1].provenance.push({...graph.nodes[1].provenance[0]});
  graph.edges.push({...graph.edges[0],id:'another-edge'});
  const rows=build(graph,context)[0].entries;
  assert.equal(rows.filter(row=>row.nodeId==='q-early').length,1);
  assert.deepEqual(rows[0].answers[0].edgeIds,['answer-edge','another-edge']);
});
test('display derivation leaves every node, evidence, ID and relationship untouched',()=>{
  const {graph,context}=fixture();
  graph.nodes[1].provenance[0].evidence=[{messageId:'m3',quote:'<원문>','verified':true}];
  const before=JSON.stringify({graph,context});
  function freeze(value){if(value && typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}}
  freeze(graph);freeze(context);build(graph,context);
  assert.equal(JSON.stringify({graph,context}),before);
});

function addTopics(context) {
  context.s.analysis.structure=[
    {id:'root',kind:'root',childIds:['storage','search','unknown']},
    {id:'storage',kind:'topic',title:'같은 제목',parentId:'root',childIds:['nested','same-turn']},
    {id:'nested',kind:'topic',title:'하위 주제',parentId:'storage',childIds:['q-early','answer']},
    {id:'search',kind:'topic',title:'같은 제목',parentId:'root',childIds:['q-late']},
    {id:'q-early',kind:'item',parentId:'nested'},
    {id:'answer',kind:'item',parentId:'nested'},
    {id:'q-late',kind:'item',parentId:'search'},
    {id:'same-turn',kind:'item',parentId:'storage'},
    {id:'unknown',kind:'item',parentId:'root'}];
}
test('uses stored topic IDs and ancestors, preserving duplicate topic names and chronology',()=>{
  const {graph,context}=fixture();addTopics(context);
  const group=build(graph,context)[0];
  assert.equal(group.topics.length,3);
  assert.deepEqual(group.entries[0].topicPath,['storage','nested']);
  assert.equal(group.entries.find(row=>row.nodeId==='q-late').topicId,'search');
  const parent=byTopic(group,'topic:storage');
  assert.deepEqual(parent.flatMap(topic=>topic.entries.map(row=>row.nodeId)),['q-early','same-turn']);
  assert.equal(byTopic(group,'topic:search')[0].entries[0].nodeId,'q-late');
  assert.deepEqual(byTopic(group,'unassigned').flatMap(topic=>topic.entries.map(row=>row.nodeId)),['unknown']);
  assert.equal(byTopic(group).flatMap(topic=>topic.entries).length,graph.nodes.filter(node=>node.classification==='answered_question').length);
});
test('missing, cyclic, ambiguous or conflicting topic membership remains unassigned',()=>{
  for(const fault of ['missing','cycle','duplicate','conflict']) {
    const {graph,context}=fixture();addTopics(context);
    const structure=context.s.analysis.structure;
    if(fault==='missing') structure.find(node=>node.id==='q-early').parentId='absent';
    if(fault==='cycle') {structure.find(node=>node.id==='storage').parentId='nested';structure.find(node=>node.id==='root').childIds=[];}
    if(fault==='duplicate') structure.push({...structure.find(node=>node.id==='q-early')});
    if(fault==='conflict') structure.find(node=>node.id==='search').childIds.push('q-early');
    assert.equal(build(graph,context)[0].entries.find(row=>row.nodeId==='q-early').topicId,'',fault);
  }
});
test('merged graph questions retain different topic membership per source session',()=>{
  const {graph,context}=fixture();addTopics(context);
  graph.nodes[1].provenance.push({session_id:'t',source_node:'q-early'});
  context.t=JSON.parse(JSON.stringify(context.s));context.t.session.title='별도 세션';
  context.t.analysis.structure=[{id:'other-topic',kind:'topic',title:'별도 주제',childIds:['q-early']},{id:'q-early',kind:'item',parentId:'other-topic'}];
  const before=JSON.stringify({graph,context});const groups=build(graph,context);
  assert.equal(groups.find(group=>group.sessionId==='s').entries.find(row=>row.nodeId==='q-early').topicId,'nested');
  assert.equal(groups.find(group=>group.sessionId==='t').entries[0].topicId,'other-topic');
  assert.equal(JSON.stringify({graph,context}),before);
});

function withAnswerEvidence() {
  const value=fixture(),question=value.graph.nodes.find(node=>node.id==='q-late');question.status='answered';
  const evidence={messageId:'sha-evidence',index:13,sourceMessageId:'m14',quotes:['Stored assistant quotation'],evidenceCount:1};
  value.context.s.messages.push({id:'sha-evidence',index:13,role:'assistant',timestamp:''});
  value.context.s.analysis.anchors['q-late'].push({messageId:'sha-evidence',index:13,sourceMessageId:'m14'});
  value.context.s.analysis.assistantEvidence={'q-late':[evidence]};
  return {...value,evidence,question};
}

function withTopicUserEvidence() {
  const value=fixture();addTopics(value.context);
  value.graph.nodes.push(value.node('request-decision','decision'));
  const analysis=value.context.s.analysis;
  analysis.structure[0].childIds.push('review-topic');
  analysis.structure.push({id:'review-topic',kind:'topic',title:'Existing request topic',parentId:'root',childIds:['request-decision']},
    {id:'request-decision',kind:'item',parentId:'review-topic'});
  value.context.s.messages.push({id:'raw-request',index:0,role:'user',timestamp:'',hasUserContent:true});
  analysis.anchors['request-decision']=[{messageId:'raw-request',index:0,sourceMessageId:'saved-user-request'}];
  return value;
}

test('session labels never replace original titles, lineage or IDs and can be reset',()=>{
  const session={id:'s',title:'Original title',titleSource:'native',project:'p',parentSessionId:'parent',sessionKind:'subagent'};
  const before=JSON.stringify(session);
  const renamed=applySessionDisplay(session,{saved:true,session:'s',displayName:'새 표시 이름',sessionDisplay:{status:'active',revision:'a'.repeat(64)}});
  assert.equal(renamed.title,'Original title');assert.equal(sessionTitle(renamed),'새 표시 이름');
  assert.equal(renamed.parentSessionId,'parent');assert.equal(renamed.id,'s');
  const restored=applySessionDisplay(renamed,{saved:true,session:'s',displayName:null,sessionDisplay:{status:'default',revision:'b'.repeat(64)}});
  assert.equal(sessionTitle(restored),'Original title');assert.equal(restored.displayTitle,undefined);
  assert.equal(JSON.stringify(session),before);
  assert.throws(()=>applySessionDisplay(session,{saved:true,session:'foreign',displayName:'wrong',sessionDisplay:{revision:'a'.repeat(64)}}));
});

test('custom names change only selected display labels and breadcrumbs and restore the original after repeated edits',()=>{
  const {graph,context}=fixture();addTopics(context);
  context.s.session.topicDisplay={sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const group=build(graph,context)[0],before=JSON.stringify({graph,context});
  const response=displayNames=>({saved:true,session:'s',auxiliaryTopicIds:[],primaryTopicIds:[],displayNames,
    topicDisplay:{sourceMapSha256:'a'.repeat(64),revision:'1'.repeat(64)}});
  const renamed=applyTopicDisplay(group,response({storage:'새 이름'}));
  assert.equal(renamed.topics.find(t=>t.id==='storage').originalTitle,'같은 제목');
  assert.equal(renamed.topics.find(t=>t.id==='search').title,'같은 제목');
  assert.equal(orderedTopics(renamed).find(t=>t.id==='nested').title,'새 이름 › 하위 주제');
  const again=applyTopicDisplay(renamed,response({storage:'다시 수정'}));
  const restored=applyTopicDisplay(again,response({}));
  assert.equal(restored.topics.find(t=>t.id==='storage').title,'같은 제목');
  assert.equal(restored.topics.find(t=>t.id==='storage').displayTitle,undefined);
  assert.equal(restored.entries,group.entries);
  assert.equal(JSON.stringify({graph,context}),before);
});

test('projected aliases retain the original analysis name and reject invalid response names',()=>{
  const {graph,context}=fixture();addTopics(context);
  context.s.analysis.structure.find(n=>n.id==='storage').displayTitle='<img src=x> 새 이름';
  context.s.session.topicDisplay={sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const group=build(graph,context)[0],topic=group.topics.find(t=>t.id==='storage');
  assert.equal(topic.title,'<img src=x> 새 이름');assert.equal(topic.originalTitle,'같은 제목');
  for(const displayNames of [{missing:'이름'},{storage:''},{storage:12},[]]) {
    assert.throws(()=>applyTopicDisplay(group,{saved:true,session:'s',auxiliaryTopicIds:[],primaryTopicIds:[],displayNames,
      topicDisplay:{sourceMapSha256:'a'.repeat(64),revision:'1'.repeat(64)}}));
  }
});

test('acknowledged display edits preserve graph data and support restoring a child inside an auxiliary parent',()=>{
  const {graph,context}=fixture();addTopics(context);
  context.s.session.topicDisplay={sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const group=build(graph,context)[0],before=JSON.stringify({graph,context,group});
  const updated=applyTopicDisplay(group,{saved:true,session:'s',auxiliaryTopicIds:['storage'],primaryTopicIds:['nested'],
    topicDisplay:{sourceMapSha256:'a'.repeat(64),revision:'1'.repeat(64)}});
  assert.equal(updated.topics.find(t=>t.id==='storage').displayGroup,'auxiliary');
  assert.equal(updated.topics.find(t=>t.id==='nested').displayGroup,undefined);
  assert.equal(updated.entries,group.entries);
  assert.equal(JSON.stringify({graph,context,group}),before);
  context.s.analysis.structure.find(n=>n.id==='storage').displayGroup='auxiliary';
  context.s.analysis.structure.find(n=>n.id==='nested').displayGroup='primary';
  assert.equal(build(graph,context)[0].topics.find(t=>t.id==='nested').displayGroup,undefined,'a reload respects the explicit restoration');
});

test('unrelated source maps and unknown topic IDs cannot be applied as a display edit',()=>{
  const {graph,context}=fixture();addTopics(context);
  context.s.session.topicDisplay={sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const group=build(graph,context)[0],base={saved:true,session:'s',auxiliaryTopicIds:[],primaryTopicIds:[],
    topicDisplay:{sourceMapSha256:'a'.repeat(64),revision:'1'.repeat(64)}};
  for(const result of [{...base,session:'other'}, {...base,auxiliaryTopicIds:['absent']},
    {...base,auxiliaryTopicIds:['storage'],primaryTopicIds:['storage']},
    {...base,topicDisplay:{...base.topicDisplay,sourceMapSha256:'b'.repeat(64)}}])assert.throws(()=>applyTopicDisplay(group,result));
});

test('auxiliary groups use explicit source metadata, keep same-title topics separate and preserve all IDs',()=>{
  const {graph,context}=withTopicUserEvidence();
  const topic=context.s.analysis.structure.find(n=>n.id==='review-topic');topic.displayGroup='auxiliary';
  context.s.analysis.structure.find(n=>n.id==='search').title=topic.title;
  const before=JSON.stringify({graph,context}),group=build(graph,context)[0];
  assert.deepEqual(topicsFor(group,'auxiliary').map(t=>t.id),['review-topic']);
  assert.ok(topicsFor(group,'primary').some(t=>t.id==='search'));
  assert.equal(topicsFor(group,'primary').some(t=>t.id==='review-topic'),false);
  assert.deepEqual(itemsForTopic(graph,group,'topic:review-topic').map(item=>[item.nodeId,item.source.source_node]),[['request-decision','request-decision']]);
  assert.equal(JSON.stringify({graph,context}),before);
});

test('primary Q&A excludes auxiliary descendants while explicit auxiliary selection keeps their questions and relations',()=>{
  const {graph,context}=fixture();addTopics(context);
  context.s.analysis.structure.find(n=>n.id==='storage').displayGroup='auxiliary';
  const group=build(graph,context)[0];
  assert.deepEqual(topicsFor(group,'auxiliary').map(t=>t.id),['storage','nested']);
  assert.deepEqual(scopedGroup(group,'primary').entries.map(e=>e.nodeId),['q-late','unknown']);
  assert.deepEqual(scopedGroup(group,'auxiliary').entries.map(e=>e.nodeId),['q-early','same-turn']);
  assert.equal(mindBranch(group,'topic:storage','','primary').questions.length,0);
  const branch=mindBranch(group,'topic:storage','','auxiliary');
  assert.equal(branch.questions.length,2);assert.equal(branch.questions[0].answers[0].edgeIds[0],'answer-edge');
});

test('a topic without question anchors can use its existing decision user evidence without becoming a question',()=>{
  const {graph,context}=withTopicUserEvidence(),before=JSON.stringify({graph,context});
  const group=build(graph,context)[0],topic=orderedTopics(group)[0];
  assert.equal(topic.id,'review-topic');assert.equal(topic.count,0);assert.equal(topic.firstQuestionIndex,null);
  assert.deepEqual(topic.userEvidence,{messageId:'raw-request',index:0,sourceMessageId:'saved-user-request',sourceNodeId:'request-decision'});
  assert.equal(group.entries.length,4);
  assert.equal(JSON.stringify({graph,context}),before);
});

test('topic evidence rejects context-only, missing eligibility, assistant-only and stale locations',()=>{
  for(const fault of ['context-only','legacy','assistant','stale','no-anchor']) {
    const {graph,context}=withTopicUserEvidence(),message=context.s.messages.at(-1);
    if(fault==='context-only')message.hasUserContent=false;
    if(fault==='legacy')delete message.hasUserContent;
    if(fault==='assistant')message.role='assistant';
    if(fault==='stale')message.index=100;
    if(fault==='no-anchor')context.s.analysis.anchors['request-decision']=[];
    const topic=orderedTopics(build(graph,context)[0]).find(t=>t.id==='review-topic');
    assert.equal(topic.userEvidence,undefined,fault);
    assert.equal(topic.firstQuestionIndex,null,fault);
  }
});

test('confirmed question positions keep priority over an earlier non-question user statement',()=>{
  const {graph,context}=withTopicUserEvidence(),structure=context.s.analysis.structure;
  structure.find(n=>n.id==='review-topic').childIds=[];
  structure.find(n=>n.id==='request-decision').parentId='storage';
  structure.find(n=>n.id==='storage').childIds.push('request-decision');
  const topic=orderedTopics(build(graph,context)[0]).find(t=>t.id==='storage');
  assert.equal(topic.firstQuestionIndex,2);
  assert.equal(topic.userEvidence,undefined);
});

test('topic user evidence includes descendants but never crosses an ambiguous hierarchy',()=>{
  const {graph,context}=withTopicUserEvidence(),structure=context.s.analysis.structure;
  structure.find(n=>n.id==='review-topic').childIds=['review-child'];
  structure.push({id:'review-child',kind:'topic',parentId:'review-topic',childIds:['request-decision']});
  structure.find(n=>n.id==='request-decision').parentId='review-child';
  let topics=orderedTopics(build(graph,context)[0]);
  assert.equal(topics.find(t=>t.id==='review-topic').userEvidence.index,0);
  assert.equal(topics.find(t=>t.id==='review-child').userEvidence.index,0);
  structure.find(n=>n.id==='search').childIds.push('request-decision');
  topics=orderedTopics(build(graph,context)[0]);
  assert.equal(topics.find(t=>t.id==='review-topic').userEvidence,undefined);
  assert.equal(topics.find(t=>t.id==='review-child').userEvidence,undefined);
});

test('answered-question evidence is separate from answers and never creates an inferred relationship',()=>{
  const {graph,context,evidence}=withAnswerEvidence(),before=JSON.stringify({graph,context});
  const row=build(graph,context)[0].entries.find(item=>item.nodeId==='q-late');
  assert.deepEqual(row.answers,[]);
  assert.deepEqual(row.answerEvidence,[evidence]);
  assert.equal(JSON.stringify({graph,context}),before);
  assert.equal(graph.edges.length,1);
});

test('assistant context on open or unconfirmed questions does not become answer evidence',()=>{
  for(const update of [{classification:'open_question'},{status:'open'}]) {
    const {graph,context,question}=withAnswerEvidence();Object.assign(question,update);
    assert.deepEqual(build(graph,context)[0].entries.find(item=>item.nodeId==='q-late').answerEvidence,[]);
  }
  const {graph,context}=withAnswerEvidence();delete context.s.analysis.assistantEvidence;
  assert.deepEqual(build(graph,context)[0].entries.find(item=>item.nodeId==='q-late').answerEvidence,[],'nearby assistant messages alone are not answers');
});

test('answer quotations are deduplicated by original message and checked against their exact source anchors',()=>{
  const {graph,context,evidence}=withAnswerEvidence();
  context.s.analysis.assistantEvidence['q-late'].push({...evidence,quotes:['Stored assistant quotation','Another stored quotation'],evidenceCount:2});
  let row=build(graph,context)[0].entries.find(item=>item.nodeId==='q-late');
  assert.equal(row.answerEvidence.length,1);
  assert.equal(row.answerEvidence[0].quotes.length,2);
  assert.equal(row.answerEvidence[0].evidenceCount,2);
  context.s.analysis.anchors['q-late'][1].sourceMessageId='wrong-id';
  row=build(graph,context)[0].entries.find(item=>item.nodeId==='q-late');
  assert.deepEqual(row.answerEvidence,[]);
});

test('an answer message already represented by a stored answers relationship is not repeated as evidence',()=>{
  const {graph,context}=fixture();graph.nodes.find(node=>node.id==='q-early').status='answered';
  context.s.analysis.assistantEvidence={'q-early':[{messageId:'sha-answer',index:3,sourceMessageId:'m4',quotes:['Stored answer'],evidenceCount:1}]};
  const row=build(graph,context)[0].entries.find(item=>item.nodeId==='q-early');
  assert.equal(row.answers.length,1);assert.deepEqual(row.answerEvidence,[]);
  assert.deepEqual(row.answers[0].edgeIds,['answer-edge']);
});

test('offline evidence metadata keeps the source location without inventing or embedding a quotation',()=>{
  const {graph,context,evidence}=withAnswerEvidence();delete evidence.quotes;
  const row=build(graph,context)[0].entries.find(item=>item.nodeId==='q-late');
  assert.equal(row.answerEvidence[0].messageId,'sha-evidence');
  assert.equal(row.answerEvidence[0].sourceMessageId,'m14');
  assert.equal(row.answerEvidence[0].evidenceCount,1);
  assert.deepEqual(row.answerEvidence[0].quotes,[]);
});
