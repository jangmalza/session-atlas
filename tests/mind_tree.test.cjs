const {test} = require('node:test');
const assert = require('node:assert/strict');
const MindTree = require('../mind_tree.js');

const leaf = (key) => ({key, children: []});
const branch = (key, children, expanded = true) => ({key, expanded, children});
const options = {rowHeight: 10, rowGap: 2, levelGap: 5, groupGap: 4, levelWidths: [20, 30, 40]};
const at = (plan, key) => plan.nodes.find(entry => entry.node.key === key);

test('visible rows stack top to bottom and parents sit level with the middle of their children', () => {
  const root = branch('root', [branch('a', [leaf('a1'), leaf('a2'), leaf('a3')]), leaf('b')]);
  const plan = MindTree.layout(root, options);
  assert.deepEqual(plan.nodes.map(entry => entry.node.key), ['root', 'a', 'a1', 'a2', 'a3', 'b']);
  assert.deepEqual(['a1', 'a2', 'a3'].map(key => at(plan, key).y), [0, 12, 24]);
  assert.equal(at(plan, 'a').y, 12, 'a parent is centred on its first and last child');
  assert.equal(at(plan, 'b').y, 24 + 12 + 4, 'an open branch keeps a group gap from the next sibling');
  assert.equal(at(plan, 'root').y, (12 + 40) / 2);
  assert.deepEqual(['root', 'a', 'a1'].map(key => at(plan, key).x), [0, 25, 60], 'x grows by level width plus gap');
  assert.deepEqual(['root', 'a', 'a1'].map(key => at(plan, key).width), [20, 30, 40]);
  assert.equal(plan.width, 100);
  assert.equal(plan.height, 50);
  assert.deepEqual(plan.links.map(link => link.from.node.key + '>' + link.to.node.key), ['root>a', 'a>a1', 'a>a2', 'a>a3', 'root>b']);
});

test('collapsed branches take one row and their children are neither placed nor linked', () => {
  const root = branch('root', [branch('a', [leaf('a1'), leaf('a2')], false), branch('b', [leaf('b1')])]);
  const plan = MindTree.layout(root, options);
  assert.deepEqual(plan.nodes.map(entry => entry.node.key), ['root', 'a', 'b', 'b1']);
  assert.equal(at(plan, 'a').y, 0);
  assert.equal(at(plan, 'b').y, 12 + 4, 'the gap precedes an open branch after a closed one');
  assert.equal(at(plan, 'b1').y, 16);
  assert.deepEqual(plan.links.map(link => link.to.node.key), ['a', 'b', 'b1']);
});

test('deeper levels reuse the last configured width and a lone root still has a height', () => {
  const root = branch('root', [branch('a', [branch('a1', [leaf('deep')])])]);
  const plan = MindTree.layout(root, options);
  assert.equal(at(plan, 'deep').width, 40);
  assert.equal(at(plan, 'deep').x, 25 + 35 + 45);
  assert.equal(plan.width, 25 + 35 + 45 + 40);
  const alone = MindTree.layout(leaf('only'), options);
  assert.equal(alone.height, 10);
  assert.equal(alone.width, 20);
  assert.deepEqual(alone.links, []);
});

test('layout never mutates the node specs it reads', () => {
  const root = branch('root', [branch('a', [leaf('a1')]), leaf('b')]);
  const before = JSON.stringify(root);
  MindTree.layout(root, options);
  assert.equal(JSON.stringify(root), before);
});

test('numbering and visible order follow the open branches only', () => {
  assert.equal(MindTree.number('', 0), '1');
  assert.equal(MindTree.number('2', 3), '2.4');
  assert.equal(MindTree.number('2.4', 0), '2.4.1');
  const root = branch('root', [branch('a', [leaf('a1')], false), branch('b', [leaf('b1'), leaf('b2')])]);
  assert.deepEqual(MindTree.visible(root).map(node => node.key), ['root', 'a', 'b', 'b1', 'b2']);
});
