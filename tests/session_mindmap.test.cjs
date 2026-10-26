const {test} = require('node:test');
const assert = require('node:assert/strict');
const {mindBranch, occurrenceKey, orderedTopics} = require('../graph_view.js');

function fixture() {
  const entry = (nodeId, sourceId, topicPath, index, answers = []) => ({
    nodeId,
    source: {session_id: 'session-a', source_node: sourceId},
    topicPath,
    topicId: topicPath.at(-1) || '',
    anchor: index === null ? null : {messageId: `message-${index}`, index},
    answers,
  });
  return {
    sessionId: 'session-a',
    title: '가상 세션',
    topics: [
      {id: 'storage', title: '저장', path: ['storage']},
      {id: 'nested', title: '수집', path: ['storage', 'nested']},
      {id: 'search', title: '저장', path: ['search']},
      {id: 'empty', title: '질문 없는 주제', path: ['empty']},
    ],
    entries: [
      entry('question-early', 'source-early', ['storage', 'nested'], 2),
      entry('question-search', 'source-search', ['search'], 4),
      entry('merged-question', 'source-first', ['storage'], 6, [
        {nodeId: 'answer-first', edgeIds: ['edge-first', 'edge-first-duplicate'], anchor: {messageId: 'message-7', index: 7}},
      ]),
      entry('merged-question', 'source-second', ['storage', 'nested'], 8, [
        {nodeId: 'answer-second', edgeIds: ['edge-second'], anchor: null},
      ]),
      entry('question-unknown', 'source-unknown', [], null),
    ],
  };
}

test('missing sessions have no branches or implicit selected questions', () => {
  const empty = {topics: [], questions: [], question: null, answers: [], answerEvidence: []};
  assert.deepEqual(mindBranch(), empty);
  assert.deepEqual(mindBranch(null, '', 'missing'), empty);
});

test('session branch keeps saved topic identities and zero-question topics', () => {
  const group = fixture();
  const branch = mindBranch(group);
  const topic = id => branch.topics.find(item => item.id === id);
  assert.equal(branch.topics.length, 5);
  assert.deepEqual(branch.questions, []);
  assert.equal(branch.question, null);
  assert.deepEqual(branch.answers, []);
  assert.equal(topic('storage').value, 'topic:storage');
  assert.equal(topic('storage').count, 3);
  assert.equal(topic('nested').count, 2);
  assert.equal(topic('search').count, 1);
  assert.equal(topic('empty').count, 0);
  assert.deepEqual(topic(''), {value: 'unassigned', title: '주제 미확정', count: 1, id: '', firstQuestionIndex: null});
  // Equal names remain separate branches because their stored IDs differ.
  assert.notEqual(topic('storage').value, topic('search').value);
});

test('topics use the earliest confirmed question including descendants, independent of stored or entry order', () => {
  const group=fixture();
  group.topics=[group.topics[2],group.topics[3],group.topics[1],group.topics[0]];
  group.entries.reverse();
  const before=JSON.stringify(group),topics=orderedTopics(group);
  assert.deepEqual(topics.map(topic=>topic.id),['nested','storage','search','empty','']);
  assert.deepEqual(topics.map(topic=>topic.firstQuestionIndex),[2,2,4,null,null]);
  assert.equal(topics[1].count,3,'a parent includes its descendant questions');
  assert.equal(JSON.stringify(group),before);
});

test('topic chronology uses verified positions when timestamps are missing or disagree with source order', () => {
  const group=fixture();
  group.entries[0].timestamp='';
  group.entries[1].timestamp='2000-01-01T00:00:00Z';
  group.entries[0].anchor.index=0;
  assert.deepEqual(orderedTopics(group).slice(0,3).map(topic=>[topic.id,topic.firstQuestionIndex]),
    [['storage',0],['nested',0],['search',4]]);
});

test('topics without confirmed positions stay last, including zero-question topics, in saved order', () => {
  const group=fixture();
  group.topics=[group.topics[3],group.topics[2],group.topics[0],group.topics[1]];
  group.entries[1].anchor=null;
  const topics=orderedTopics(group);
  assert.deepEqual(topics.map(topic=>topic.id),['storage','nested','empty','search','']);
  assert.deepEqual(topics.slice(2).map(topic=>topic.firstQuestionIndex),[null,null,null]);
  assert.equal(topics.find(topic=>topic.id==='search').count,1);
});

test('invalid positions cannot make an unconfirmed topic appear chronological', () => {
  for(const index of [-1,1.5,Infinity,NaN,'0']) {
    const group=fixture();group.entries[1].anchor.index=index;
    assert.equal(orderedTopics(group).find(topic=>topic.id==='search').firstQuestionIndex,null);
  }
});

test('unassigned membership can still have confirmed question order without changing its identity', () => {
  const group=fixture();group.entries[4].anchor={index:1,messageId:'unassigned-original'};
  const topic=orderedTopics(group)[0];
  assert.deepEqual(topic,{id:'',value:'unassigned',title:'주제 미확정',count:1,firstQuestionIndex:1});
  assert.equal(mindBranch(group,'unassigned').questions[0],group.entries[4]);
});

test('a selected question and its relation IDs survive a change in topic display rank', () => {
  const group=fixture(),entry=group.entries[2];
  group.topics.reverse();
  const branch=mindBranch(group,'topic:storage',occurrenceKey(entry));
  assert.equal(branch.question,entry);
  assert.deepEqual(branch.answers[0].edgeIds,['edge-first','edge-first-duplicate']);
  assert.equal(branch.topics.find(topic=>topic.id==='storage').value,'topic:storage');
});

test('topic selection includes descendants and preserves original question order', () => {
  const group = fixture();
  assert.deepEqual(mindBranch(group, '').questions, group.entries);
  assert.deepEqual(mindBranch(group, 'topic:storage').questions,
    [group.entries[0], group.entries[2], group.entries[3]]);
  assert.deepEqual(mindBranch(group, 'topic:nested').questions,
    [group.entries[0], group.entries[3]]);
  assert.deepEqual(mindBranch(group, 'unassigned').questions, [group.entries[4]]);
  assert.deepEqual(mindBranch(group, 'topic:empty').questions, []);
  assert.deepEqual(mindBranch(group, 'topic:absent').questions, []);
});

test('merged graph nodes retain exact source occurrences and their own answers', () => {
  const group = fixture(), first = group.entries[2], second = group.entries[3];
  const firstKey = occurrenceKey(first), secondKey = occurrenceKey(second);
  assert.equal(firstKey, JSON.stringify(['session-a', 'source-first', 'merged-question']));
  assert.notEqual(firstKey, secondKey);
  const foreign = {...first, source: {...first.source, session_id: 'session-b'}};
  assert.notEqual(firstKey, occurrenceKey(foreign));
  const selected = mindBranch(group, 'topic:storage', secondKey);
  assert.deepEqual(selected.question, second);
  assert.deepEqual(selected.answers, second.answers);
  assert.equal(mindBranch(group, '', occurrenceKey(foreign)).question, null);
});

test('changing to a different topic clears an out-of-branch question and answers', () => {
  const group = fixture(), key = occurrenceKey(group.entries[2]);
  for (const selection of [null, 'topic:nested', 'topic:search', 'unassigned', 'topic:absent']) {
    const branch = mindBranch(group, selection, key);
    assert.equal(branch.question, null);
    assert.deepEqual(branch.answers, []);
  }
});

test('selected answers retain every stored relation ID without generating connections', () => {
  const group = fixture();
  const branch = mindBranch(group, '', occurrenceKey(group.entries[2]));
  assert.deepEqual(branch.answers, group.entries[2].answers);
  assert.deepEqual(branch.answers[0].edgeIds, ['edge-first', 'edge-first-duplicate']);
  assert.deepEqual(mindBranch(group, '', occurrenceKey(group.entries[0])).answers, []);
});

test('branch derivation does not alter source IDs, anchors, order or relationships', () => {
  const group = fixture(), before = JSON.stringify(group);
  function freeze(value) {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
  }
  freeze(group);
  for (const selection of [null, '', 'topic:storage', 'topic:nested', 'unassigned']) {
    for (const entry of group.entries) mindBranch(group, selection, occurrenceKey(entry));
  }
  assert.equal(JSON.stringify(group), before);
});
