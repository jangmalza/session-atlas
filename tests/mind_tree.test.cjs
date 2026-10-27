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

const view = {width: 400, height: 300};

test('fit scales a tree down to the viewport within its limits and centres what fits', () => {
  assert.deepEqual(MindTree.fit(view, {width: 100, height: 50}, {pad: 20}), {x: 150, y: 125, k: 1}, 'a small tree is centred at 100%');
  const wide = MindTree.fit(view, {width: 720, height: 100}, {pad: 20});
  assert.equal(wide.k, 0.5);
  assert.equal(wide.x, 20, 'a tree as wide as the viewport starts at the padding');
  assert.equal(wide.y, (300 - 50) / 2);
  const tall = MindTree.fit(view, {width: 100, height: 2600}, {pad: 20, min: 0.5, focusY: 1300});
  assert.equal(tall.k, 0.5, 'the minimum scale wins over fitting a very tall tree');
  assert.equal(tall.y, 150 - 1300 * 0.5, 'the focus row sits at the vertical middle');
  const top = MindTree.fit(view, {width: 100, height: 2600}, {pad: 20, min: 0.5, focusY: 10});
  assert.equal(top.y, 20, 'a focus near the top cannot push the tree below the padding');
});

test('zooming keeps the point under the pointer still and respects the scale limits', () => {
  const camera = {x: 10, y: 20, k: 1}, point = {x: 110, y: 70};
  const zoomed = MindTree.zoomAt(camera, point, 2);
  assert.deepEqual(zoomed, {x: 110 - 100 * 2, y: 70 - 50 * 2, k: 2});
  const treeX = (point.x - camera.x) / camera.k, treeXAfter = (point.x - zoomed.x) / zoomed.k;
  assert.equal(treeX, treeXAfter);
  assert.equal(MindTree.zoomAt(camera, point, 100).k, 2.5);
  assert.equal(MindTree.zoomAt(camera, point, 0.01).k, 0.25);
});

test('reveal moves the camera as little as needed and shows the start of a box that cannot fit', () => {
  const camera = {x: 0, y: 0, k: 1};
  assert.deepEqual(MindTree.reveal(camera, view, {x: 100, y: 100, width: 50, height: 30}, {pad: 20}), camera, 'a visible box leaves the camera alone');
  assert.deepEqual(MindTree.reveal(camera, view, {x: 380, y: 290, width: 50, height: 30}, {pad: 20}), {x: -50, y: -40, k: 1}, 'an edge box is pulled in by its overflow');
  assert.deepEqual(MindTree.reveal({x: -200, y: -200, k: 1}, view, {x: 100, y: 100, width: 50, height: 30}, {pad: 20}), {x: -80, y: -80, k: 1});
  const large = MindTree.reveal({x: -300, y: -300, k: 1}, view, {x: 40, y: 60, width: 1000, height: 1000}, {pad: 20});
  assert.deepEqual(large, {x: 20 - 40, y: 20 - 60, k: 1}, 'an oversized box is aligned to its top-left so its first rows show');
});

test('clamp keeps part of the tree inside the viewport and bounds covers a set of rows', () => {
  const size = {width: 500, height: 400};
  assert.deepEqual(MindTree.clamp({x: 900, y: 900, k: 1}, view, size, {keep: 48}), {x: 352, y: 252, k: 1});
  assert.deepEqual(MindTree.clamp({x: -900, y: -900, k: 0.5}, view, size, {keep: 48}), {x: 48 - 250, y: 48 - 200, k: 0.5});
  assert.deepEqual(MindTree.bounds([{x: 10, y: 40, width: 100, height: 30}, {x: 150, y: 10, width: 60, height: 30}]), {x: 10, y: 10, width: 200, height: 60});
});
