const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the entire production browser script. This small DOM implements only
// browser primitives; it deliberately contains no catalog or selection logic.
const source = ['explorer_tools.js','raw_turns.js','mind_tree.js','graph_view.js'].map(name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8')).join('\n');
const html = fs.readFileSync(path.join(__dirname, '../graph_view.html'), 'utf8');
const stateKey = 'context-graph-mind-selection';
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase(); this.document = document;
    this.children = []; this.parentElement = null; this.dataset = {};
    this.attributes = new Map(); this.listeners = new Map(); this._text = '';
    this.value = ''; this.disabled = false; this.hidden = false; this.checked = false;
    this.className = ''; this.offsetLeft = this.offsetTop = 0;
    this.scrollTop = this.scrollLeft = 0;
    this.offsetWidth = this.clientWidth = 240; this.offsetHeight = this.clientHeight = 100;
    this.style = {setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; }};
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
      toggle: (name, force) => {
        const present = force === undefined ? !this.classList.contains(name) : force;
        this.classList[present ? 'add' : 'remove'](name); return present;
      },
    };
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get options() { return this.children.flatMap(child => child.tagName === 'OPTION' ? [child] : child.tagName === 'OPTGROUP' ? child.options : []); }
  get offsetParent() { return this.parentElement; }
  append(...children) {
    for (const child of children) { child.parentElement = this; this.children.push(child); }
  }
  replaceChildren(...children) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = []; this._text = ''; this.append(...children);
  }
  remove() {
    if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this);
    this.parentElement = null;
  }
  setAttribute(name, value) {
    value = String(value); this.attributes.set(name, value);
    if (name === 'class') this.className = value;
    if (name === 'id') this.id = value;
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
  }
  getAttribute(name) { return name === 'class' ? this.className : this.attributes.get(name) ?? null; }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  removeEventListener(type, handler) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler));
  }
  dispatchEvent(event) {
    event.target ||= this;
    for (const handler of this.listeners.get(event.type) || []) handler(event);
    return true;
  }
  click() { if (!this.disabled) { this.focus(); this.dispatchEvent({type: 'click'}); } }
  focus() { this.document.activeElement = this; }
  scrollIntoView(options) { this.document.lastScrolled=this;this.document.lastScrollOptions=options; }
  getBoundingClientRect() {
    let left=0,top=0;
    for(let node=this;node;node=node.parentElement) {left+=node.offsetLeft;top+=node.offsetTop;if(node.parentElement){left-=node.parentElement.scrollLeft;top-=node.parentElement.scrollTop;}}
    return {x:left,y:top,left,top,width:this.offsetWidth,height:this.offsetHeight,right:left+this.offsetWidth,bottom:top+this.offsetHeight};
  }
  matches(selector) {
    const attribute = selector.match(/\[([^=\]]+)(?:="([^"]*)")?\]/);
    if (attribute && (attribute[2] === undefined ? this.getAttribute(attribute[1]) === null : this.getAttribute(attribute[1]) !== attribute[2])) return false;
    selector = selector.replace(/\[[^\]]*\]/g, '');
    const id = selector.match(/#([\w-]+)/), cls = selector.match(/\.([\w-]+)/), tag = selector.match(/^[\w-]+/);
    return (!id || this.id === id[1]) && (!cls || this.classList.contains(cls[1])) && (!tag || this.tagName === tag[0].toUpperCase());
  }
  querySelectorAll(selectors) {
    const result = [], choices = selectors.split(',');
    const visit = node => { for (const child of node.children) { if (choices.some(selector => child.matches(selector))) result.push(child); visit(child); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
}

function fixture() {
  const sessionId = 'session-36';
  const sessions = Array.from({length: 48}, (_, index) => ({id: `session-${index}`, title: `Synthetic session ${index}`, project: 'test', hasAnalysis: true, messageCount: 32, importedAt: 'synthetic-revision'}));
  const session = sessions[36];
  const structure = [{id: 'root', kind: 'root', childIds: Array.from({length: 18}, (_, index) => `topic-${index}`)}];
  for (let index = 0; index < 18; index++) structure.push({id: `topic-${index}`, kind: 'topic', title: `Topic ${index}`, parentId: 'root', childIds: index === 12 ? Array.from({length: 16}, (_, i) => `source-q${i}`) : []});
  const nodes = [], messages = [], anchors = {};
  for (let index = 0; index < 16; index++) {
    nodes.push({id: `q${index}`, title: `Question ${index}`, summary: '', project: 'test', classification: 'open_question', provenance: [{session_id: sessionId, source_node: `source-q${index}`, evidence: []}]});
    structure.push({id: `source-q${index}`, kind: 'item', parentId: 'topic-12', childIds: []});
    messages.push({id: `m${index}`, index, role: 'user', timestamp: `2026-10-23T00:00:${String(index).padStart(2, '0')}.000Z`, text: 'Synthetic question'});
    anchors[`source-q${index}`] = [{messageId: `m${index}`, sourceMessageId: `original-${index}`, index}];
  }
  const context = {session, analysis: {structure, anchors, relations: []}, messages};
  const graph = {nodes, edges: [], projects: ['test'], sessionCount: 1};
  const selection = {session: sessionId, topic: 'topic:topic-12', question: JSON.stringify([sessionId, 'source-q12', 'q12']), page: 4, filters: {query: '', project: ''}};
  return {sessions, sessionId, context, graph, selection};
}

async function settle() { for (let index = 0; index < 20; index++) await Promise.resolve(); }

function browser({data = fixture(), storage = new Map(), seed = {[data.sessionId]: data.context}, protocol = 'http:', catalog, messages, displayEdit, sessionEdit, itemEdit, searchResults} = {}) {
  const elements = new Map();
  const document = new Element('document'); document.document = document;
  document.body = new Element('body', document); document.append(document.body);
  document.visibilityState = 'visible'; document.hidden = false;
  document.createElement = tag => new Element(tag, document);
  document.createElementNS = (_, tag) => new Element(tag, document);
  document.createDocumentFragment = () => new Element('fragment', document);
  document.getElementById = id => elements.get(id) || null;
  for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const element = new Element(match[1], document); element.setAttribute('id', match[2]);
    elements.set(match[2], element); document.body.append(element);
  }
  for (const [parentId, childId] of [['mind-canvas', 'mind-session-picker'], ['mind-canvas', 'mind-tree-host'], ['mind-session-picker', 'mind-sessions'], ['mind-tree-host', 'mind-tree'], ['mind-tree', 'mind-links']]) {
    const parent = elements.get(parentId), child = elements.get(childId);
    document.body.children = document.body.children.filter(item => item !== child); parent.append(child);
  }
  elements.get('graph-data').textContent = JSON.stringify(data.graph);
  elements.get('qa-context').textContent = JSON.stringify(seed);
  for(const id of ['item-title-dialog','explorer-search-dialog','explorer-library']){const dialog=elements.get(id);dialog.open=false;dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;};}
  for (const id of ['project']) elements.get(id).append(new Element('option', document));
  const sessionStorage = {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key)};
  let now = 0, timerId = 0, frameId = 0;
  const timers = new Map(), frames = new Map(), requests = [], navigation = [], displayRequests = [], sessionRequests = [], itemRequests = [];
  const schedule = (handler, delay = 0) => { const id = ++timerId; timers.set(id, {at: now + delay, handler}); return id; };
  const clearTimer = id => timers.delete(id);
  const location = {protocol, origin: 'http://127.0.0.1:64391', href: `${protocol}//127.0.0.1:64391/graph.html?mode=mind`, pathname: '/graph.html', search: '?mode=mind', hash: '', assign: target => navigation.push(target)};
  const window = new Element('window', document);
  Object.assign(window, {location, document, sessionStorage, setTimeout: schedule, clearTimeout: clearTimer});
  window.parent = {postMessage: value => navigation.push(clone(value))};
  window.matchMedia = () => ({matches: false, addEventListener() {}});
  let catalogHandler = catalog || (() => ({sessions: clone(data.sessions)}));
  const response = value => ({ok: true, json: async () => clone(value)});
  const fetch = async (url, options = {}) => {
    requests.push(String(url));
    if(String(url)==='timeline/search.json') {assert.equal(options.method,'POST');assert.ok(searchResults,'only explicit search tests may search');return response(await searchResults(JSON.parse(options.body)));}
    if(String(url)==='item-display.json') {
      assert.equal(options.method,'POST');assert.equal(options.headers['X-Context-Graph-Action'],'item-display');
      assert.equal(options.headers['Content-Type'],'application/json');assert.ok(itemEdit,'only explicit item tests may save titles');
      const value=JSON.parse(options.body);itemRequests.push(value);return itemEdit(value,options);
    }
    if(String(url)==='session-display.json') {
      assert.equal(options.method,'POST');assert.equal(options.headers['X-Context-Graph-Action'],'session-display');
      assert.equal(options.headers['Content-Type'],'application/json');assert.ok(sessionEdit,'only explicit name tests may save session labels');
      const value=JSON.parse(options.body);sessionRequests.push(value);return sessionEdit(value,options);
    }
    if(String(url)==='topic-display.json') {
      assert.equal(options.method,'POST');assert.equal(options.headers['X-Context-Graph-Action'],'topic-display');
      assert.equal(options.headers['Content-Type'],'application/json');
      assert.ok(displayEdit,'only explicit edit tests may write local preferences');
      const value=JSON.parse(options.body);displayRequests.push(value);return displayEdit(value,options);
    }
    assert.match(String(url), /^timeline\/(sessions|messages|context|turns)\.json(?:\?|$)/, 'UI must only call local read-only timeline routes');
    if(String(url).startsWith('timeline/turns.json'))return response({session:{id:new URL(url,'http://local/').searchParams.get('session')},kind:'chronological',turns:[],revision:'revision-1'});
    if (String(url) === 'timeline/sessions.json') return response(await catalogHandler(options));
    return response(messages ? await messages(url, options) : {...data.context, total: data.context.messages.length, offset: 0, revision: 'revision-1'});
  };
  const sandbox = {document, window, location, sessionStorage, fetch, URL, URLSearchParams, AbortController, console,
    setTimeout: schedule, clearTimeout: clearTimer,
    requestAnimationFrame: handler => { const id = ++frameId; frames.set(id, handler); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    history: {replaceState() {}}, __KO_LABELS__: {classification: {}, relation: {}, status: {}},
  };
  Object.assign(window, {fetch, requestAnimationFrame: sandbox.requestAnimationFrame, cancelAnimationFrame: sandbox.cancelAnimationFrame});
  vm.runInNewContext(source, sandbox, {filename: 'graph_view.js'});
  return {
    data, storage, document, window, requests, navigation, elements, timers, displayRequests, sessionRequests, itemRequests,
    setCatalog: handler => { catalogHandler = handler; },
    state: () => JSON.parse(storage.get(stateKey) || 'null'),
    // The session list is the picker; every other node is a branch of the tree.
    sessions: () => elements.get('mind-sessions').querySelectorAll('.mind-node'),
    nodes: kind => elements.get('mind-tree').querySelectorAll('.mind-node').filter(node => node.dataset.kind === kind),
    answers: () => elements.get('mind-tree').querySelectorAll('.mind-node').filter(node => ['answer', 'answer-evidence'].includes(node.dataset.kind)),
    selected: kind => [...elements.get('mind-tree').querySelectorAll('.mind-node'), ...elements.get('mind-sessions').querySelectorAll('.mind-node')]
      .find(node => node.dataset.kind === kind && node.getAttribute('aria-pressed') === 'true') || null,
    edges: () => elements.get('mind-links').querySelectorAll('path'),
    // Branch lines are drawn on the next animation frame; tests run that frame explicitly.
    draw() { for (const [id, handler] of [...frames]) { frames.delete(id); handler(); } },
    openList() { if (elements.get('mind-session-change').textContent === '세션 목록 펼치기') elements.get('mind-session-change').click(); },
    pager: direction => elements.get('mind-sessions').querySelectorAll('button').find(button => button.getAttribute('aria-label') === `${direction === 'next' ? '다음' : '이전'} 세션 노드`),
    async advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const first = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!first) break;
        now = first[1].at; timers.delete(first[0]); first[1].handler(); await settle();
      }
      now = end; await settle();
    },
  };
}

test('full startup keeps restored page four while a partial catalog is replaced asynchronously', async () => {
  const data = fixture(), pending = deferred(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage, catalog: () => pending.promise});
  await settle();
  assert.deepEqual(app.state(), data.selection, 'partial saved context must not overwrite restored pages');
  pending.resolve({sessions: data.sessions}); await settle();
  assert.equal(app.selected('session')?.dataset.key, data.sessionId);
  assert.equal(app.selected('topic')?.dataset.key, data.selection.topic);
  assert.equal(app.selected('question')?.dataset.key, data.selection.question);
  assert.deepEqual(app.state(), data.selection);
  assert.equal(app.elements.get('mind-tree-host').hidden, false);
  app.openList();
  assert.match(app.elements.get('mind-sessions').textContent, /33–40 \/ 48/, 'the restored session page survives until the list is opened');
});

function panelResponse(data,url) {
  const query=new URL(url,'http://local/').searchParams,limit=Number(query.get('limit')||200);
  const target=data.context.messages.findIndex(m=>m.id===query.get('message'));
  const offset=query.has('offset')?Number(query.get('offset')):Math.max(0,Math.min(target-3,data.context.messages.length-limit));
  return {...data.context,offset,total:data.context.messages.length,revision:'stable',messages:data.context.messages.slice(offset,offset+limit).map(m=>({...m,content:'LITERAL <img src=x> '+m.id}))};
}
test('question stepping crosses pages, opens exact raw context and preserves data and display names',async()=>{
  const data=fixture(),before=clone(data),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const app=browser({data,storage,messages:url=>panelResponse(data,url)});await settle();
  app.elements.get('question-next').click();await settle();
  assert.equal(app.selected('question').dataset.nodeId,'q13');assert.equal(app.selected('question').getAttribute('aria-expanded'),'true');
  assert.equal(app.elements.get('source-reader').hidden,false);
  const raw=app.elements.get('source-reader-body').querySelector('.reader-target');
  assert.equal(raw.dataset.messageId,'m13');assert.match(raw.textContent,/LITERAL <img src=x> m13/);assert.equal(raw.querySelector('img'),null);
  app.elements.get('question-previous').click();await settle();assert.equal(app.selected('question').dataset.nodeId,'q12');
  assert.equal(JSON.stringify(data),JSON.stringify(before));
});
test('a delayed raw response cannot reopen the panel after changing sessions',async()=>{
  const data=fixture(),pending=deferred(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),
    messages:url=>new URL(url,'http://local/').searchParams.get('limit')==='7'?pending.promise:panelResponse(data,url)});await settle();
  app.elements.get('question-next').click();
  app.openList();app.sessions().find(c=>c.dataset.key==='session-37').click();
  pending.resolve(panelResponse(data,'http://local/?limit=7&message=m13'));await settle();
  assert.equal(app.elements.get('source-reader').hidden,true);assert.equal(app.state().session,'session-37');
});
test('favorites and last-read message survive reload without source writes',async()=>{
  const data=fixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const options={data,storage,messages:url=>panelResponse(data,url)},app=browser(options);await settle();
  app.elements.get('explorer-star').click();app.elements.get('question-next').click();await settle();
  const saved=JSON.parse(storage.get('session-atlas-navigation-v1'));
  assert.deepEqual(saved.favorites,[data.sessionId]);assert.equal(saved.positions[0].message,'m13');assert.ok(!JSON.stringify(saved).includes('LITERAL'));
  const restored=browser(options);await settle();assert.equal(restored.elements.get('explorer-star').getAttribute('aria-pressed'),'true');
  restored.elements.get('explorer-resume').click();await settle();
  assert.equal(restored.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'m13');
  assert.equal(restored.displayRequests.length+restored.sessionRequests.length+restored.itemRequests.length,0);
});
test('full-text search shows an explicit source path and opens its exact question occurrence',async()=>{
  const data=fixture(),before=clone(data),app=browser({data,messages:url=>panelResponse(data,url),
    searchResults:()=>({results:[{id:'m12',sessionId:data.sessionId,index:12,role:'user',snippet:'LITERAL <script> query',analysisNodeIds:['source-q12']}],total:1,offset:0,limit:20})});await settle();
  app.elements.get('explorer-search').click();app.elements.get('explorer-search-query').value='query';
  app.elements.get('explorer-search-form').dispatchEvent({type:'submit',preventDefault(){}});await settle();
  const results=app.elements.get('explorer-search-results');assert.equal(results.querySelector('script'),null);
  const target=results.querySelectorAll('button').find(b=>b.textContent.startsWith('마인드맵 · '));assert.ok(target);target.click();await settle();
  assert.equal(app.state().session,data.sessionId);assert.equal(app.state().topic,'topic:topic-12');
  assert.equal(app.selected('question').dataset.sourceNode,'source-q12');assert.equal(app.selected('topic').dataset.key,'topic:topic-12');
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'m12');assert.deepEqual(data,before);
});

test('session list and tree switch without losing the selected branch or pages',async()=>{
  const data=fixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)]]),app=browser({data,storage});await settle();
  assert.equal(app.elements.get('mind-session-picker').hidden,true);
  assert.equal(app.elements.get('mind-tree-host').hidden,false);
  assert.equal(app.elements.get('mind-canvas').classList.contains('session-picker-open'),false);
  assert.equal(app.selected('session').dataset.depth,'0','the chosen session is the root of the tree');
  app.elements.get('mind-session-change').click();
  assert.equal(app.elements.get('mind-session-picker').hidden,false);
  assert.equal(app.elements.get('mind-tree-host').hidden,true);
  assert.equal(app.nodes('topic').length,0);
  assert.equal(app.state().sessionListOpen,true);
  const restored=browser({data,storage});await settle();
  assert.equal(restored.elements.get('mind-session-picker').hidden,false);
  assert.ok(restored.selected('session').querySelectorAll('.session-request-preview').length>0,'the list shows request previews');
  restored.selected('session').click();
  assert.deepEqual(restored.state(),data.selection,'choosing the same session must retain its topic, question and pages');
  assert.equal(restored.elements.get('mind-session-picker').hidden,true);
  assert.equal(restored.elements.get('mind-canvas').classList.contains('session-picker-open'),false);
  assert.equal(restored.sessions().length,0);
  assert.equal(restored.selected('topic')?.dataset.key,data.selection.topic);
  const fresh=browser({data});await settle();
  assert.equal(fresh.elements.get('mind-session-picker').hidden,false);
  assert.equal(fresh.elements.get('mind-tree-host').hidden,true);
  assert.equal(fresh.elements.get('mind-session-change').hidden,true,'nothing to return to before a session is chosen');
});

test('explicit list and map toggles preserve search, project, pages and source identities without new requests',async()=>{
  const data=fixture(),selection={...data.selection,filters:{query:'Synthetic session',project:'test'}};
  const before=clone(data),app=browser({data,storage:new Map([[stateKey,JSON.stringify(selection)]])});await settle();
  const requests=app.requests.length;
  app.elements.get('mind-session-change').click();
  assert.equal(app.elements.get('query').value,selection.filters.query);
  assert.equal(app.elements.get('project').value,'test');
  assert.deepEqual(app.state(),{...selection,sessionListOpen:true});
  app.elements.get('mind-session-change').click();
  assert.deepEqual(app.state(),selection);
  assert.equal(app.elements.get('mind-tree-host').hidden,false);
  assert.equal(app.selected('question').dataset.key,selection.question);
  assert.equal(app.selected('question').dataset.parent,selection.topic,'a question branches from its topic');
  assert.equal(app.requests.length,requests);assert.deepEqual(data,before);
});

test('session picker orders by recent activity, renders literal previews and preserves the graph',async()=>{
  const data=fixture();data.sessions=data.sessions.slice(0,4);
  Object.assign(data.sessions[0],{lastMessageAt:'2026-01-01T00:00:00Z',hasAnalysis:true});
  Object.assign(data.sessions[1],{lastMessageAt:'2026-10-25T00:00:00Z',hasAnalysis:false,
    displayTitle:'사용자 표시 이름',requestPreviews:{scope:'session',first:{text:'<img src=x> FIRST',messageId:'first-id'},latest:{text:'LATEST UNIQUE',messageId:'last-id'}}});
  Object.assign(data.sessions[2],{lastMessageAt:'invalid'});
  Object.assign(data.sessions[3],{lastMessageAt:'2026-03-01T00:00:00Z'});
  const before=clone(data),app=browser({data});await settle();
  assert.deepEqual(app.sessions().map(c=>c.dataset.key),['session-1','session-3','session-0','session-2']);
  const card=app.sessions()[0];
  assert.equal(card.querySelector('.name').textContent,'사용자 표시 이름');
  const previews=card.querySelectorAll('.session-request-preview');
  assert.equal(previews.find(e=>e.dataset.preview==='first').dataset.messageId,'first-id');
  assert.match(previews.find(e=>e.dataset.preview==='latest').textContent,/LATEST UNIQUE/);
  assert.equal(card.querySelector('img'),null);
  app.elements.get('query').value='LATEST UNIQUE';app.elements.get('query').dispatchEvent({type:'input'});
  assert.deepEqual(app.sessions().map(c=>c.dataset.key),['session-1']);
  assert.deepEqual(data,before);
});

test('the session list retains its scroll offset during catalogue updates and keeps paging outside the list',async()=>{
  const data=fixture(),selection={...data.selection,sessionListOpen:true};
  const app=browser({data,storage:new Map([[stateKey,JSON.stringify(selection)]])});await settle();
  const host=app.elements.get('mind-sessions');host.querySelector('.mind-node-list').scrollTop=72;
  app.setCatalog(()=>({sessions:data.sessions.map(session=>({...session,messageCount:session.messageCount+1}))}));
  await app.advance(5000);
  assert.equal(host.querySelector('.mind-node-list').scrollTop,72);
  assert.equal(host.querySelector('.paging').parentElement,host);
  assert.deepEqual(app.state(),selection);
});

test('restored topic and question survive startup without embedded analysis context', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage, seed: {}}); await settle();
  assert.equal(app.selected('session')?.dataset.key, data.sessionId);
  assert.equal(app.selected('topic')?.dataset.key, data.selection.topic);
  assert.equal(app.selected('question')?.dataset.key, data.selection.question);
  assert.deepEqual(app.state(), data.selection);
});

test('pager-only changes persist across source navigation and a new browser script instance', async () => {
  const data = fixture(), storage = new Map();
  const app = browser({data, storage}); await settle();
  for (let page = 0; page < 4; page++) app.pager('next').click();
  app.sessions().find(card => card.dataset.key === data.sessionId).click();
  assert.equal(app.nodes('topic').length, 18, 'every topic branches from the root at once');
  assert.equal(app.nodes('question').length, 0);
  app.nodes('topic').find(card => card.dataset.key === data.selection.topic).click();
  assert.equal(app.nodes('question').length, 16, 'the open topic shows all of its questions');
  app.nodes('question').find(card => card.dataset.key === data.selection.question).click();
  const expected = {...data.selection};
  assert.deepEqual(app.state(), expected);
  app.elements.get('mind-actions').querySelector('.source-open').click(); await settle();
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'m12');
  const returned = browser({data, storage}); await settle();
  assert.deepEqual(returned.state(), expected);
  assert.equal(returned.selected('session')?.dataset.key, data.sessionId);
  assert.equal(returned.selected('question')?.dataset.key, data.selection.question);
  assert.equal(returned.nodes('topic').length, 18);
  assert.ok(returned.elements.get('mind-actions').querySelector('.source-open'));
});

test('polling adds a collected session without replacing selected topic and question DOM', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage}); await settle();
  const topic = app.selected('topic'), question = app.selected('question');
  const updated = [...data.sessions, {id: 'newly-collected', title: 'New synthetic session', project: 'test', hasAnalysis: false, messageCount: 1}];
  app.setCatalog(() => ({sessions: updated})); await app.advance(5000);
  assert.deepEqual(app.state(), data.selection);
  assert.strictEqual(app.selected('topic'), topic);
  assert.strictEqual(app.selected('question'), question);
  assert.match(app.elements.get('info').textContent, /49/);
  app.openList();
  for (let page = 4; page < 6; page++) app.pager('next').click();
  assert.ok(app.sessions().some(card => card.dataset.key === 'newly-collected'));
  assert.equal(app.requests.filter(url => url.startsWith('timeline/context.json')).length, 1, 'catalog polling must not reload original context');
});

test('unchanged polls keep visible cards, focus and selection untouched', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage}); await settle();
  const selectedSession = app.selected('session'), question = app.selected('question'); question.focus();
  await app.advance(15000);
  assert.strictEqual(app.selected('session'), selectedSession);
  assert.strictEqual(app.selected('question'), question);
  assert.strictEqual(app.document.activeElement, question);
  assert.deepEqual(app.state(), data.selection);
  assert.ok(app.requests.filter(url => url === 'timeline/sessions.json').length >= 3);
});

test('failed catalog refresh preserves visible data and retries successfully', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage}); await settle();
  const selectedSession = app.selected('session');
  app.setCatalog(() => { throw new Error('Synthetic unavailable local reader'); });
  await app.advance(5000);
  assert.strictEqual(app.selected('session'), selectedSession);
  assert.deepEqual(app.state(), data.selection);
  app.setCatalog(() => ({sessions: [...data.sessions, {id: 'recovery', project: 'test', hasAnalysis: false}]}));
  await app.advance(5000);
  assert.match(app.elements.get('info').textContent, /49/);
  assert.deepEqual(app.state(), data.selection);
});

test('catalog failure at startup does not discard a restored later page before recovery', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage, catalog: () => { throw new Error('Synthetic offline reader'); }}); await settle();
  assert.deepEqual(app.state(), data.selection);
  app.setCatalog(() => ({sessions: data.sessions})); await app.advance(5000);
  assert.equal(app.selected('session')?.dataset.key, data.sessionId);
  assert.equal(app.selected('question')?.dataset.key, data.selection.question);
  assert.deepEqual(app.state(), data.selection);
});

test('standalone file exports never fetch or schedule catalog polling', async () => {
  const data = fixture(), app = browser({data, protocol: 'file:'}); await settle();
  await app.advance(60000);
  assert.deepEqual(app.requests, []);
  assert.equal(app.timers.size, 0);
  assert.equal(app.sessions().length, 1);
});

test('pagehide stops catalog polling for a discarded viewer', async () => {
  const app = browser(); await settle();
  const count = app.requests.length;
  app.window.dispatchEvent({type: 'pagehide'}); await app.advance(30000);
  assert.equal(app.requests.length, count);
});

test('an unanalyzed session on page five restores after context loading finishes before its catalog', async () => {
  const data = fixture(), pending = deferred();
  data.sessions = Array.from({length: 80}, (_, index) => ({id: `session-${index}`, title: `Synthetic session ${index}`, project: 'test', hasAnalysis: index === 36}));
  const selection = {session: 'session-33', topic: null, question: '', page: 4, filters: {query: '', project: ''}};
  const storage = new Map([[stateKey, JSON.stringify(selection)]]);
  const app = browser({data, storage, catalog: () => pending.promise}); await settle();
  assert.deepEqual(app.state(), selection);
  assert.equal(app.selected('session'), null, 'the partial catalog only contains the analyzed session');
  pending.resolve({sessions: data.sessions}); await settle();
  assert.equal(app.selected('session')?.dataset.key, selection.session);
  assert.equal(app.elements.get('mind-canvas').classList.contains('raw-chronology'), true);
  const original = app.elements.get('mind-actions').querySelector('button');
  assert.ok(original, 'the delayed catalog must also render the restored session original-conversation action');
  assert.equal(original.disabled, false); original.click(); await settle();
  assert.match(app.requests.at(-1), new RegExp('session='+selection.session+'&limit=100'));
  const returned = browser({data, storage}); await settle();
  assert.equal(returned.selected('session')?.dataset.key, selection.session);
  assert.deepEqual(returned.state(), selection);
});

test('project and text filters restore the same page even when the project exists only in the live catalog', async () => {
  const data = fixture(), pending = deferred();
  data.sessions = data.sessions.map((session, index) => ({...session, hasAnalysis: index === 36, project: index < 16 ? 'live-only' : 'test', title: index < 16 ? `Matched ${index}` : session.title}));
  const selection = {session: 'session-10', topic: null, question: '', page: 1, filters: {query: 'Matched', project: 'live-only'}};
  const storage = new Map([[stateKey, JSON.stringify(selection)]]);
  const app = browser({data, storage, catalog: () => pending.promise}); await settle();
  assert.equal(app.elements.get('project').value, 'live-only');
  pending.resolve({sessions: data.sessions}); await settle();
  assert.equal(app.elements.get('query').value, 'Matched');
  assert.equal(app.elements.get('project').value, 'live-only');
  assert.equal(app.selected('session')?.dataset.key, selection.session);
  app.openList();
  assert.match(app.elements.get('mind-sessions').textContent, /9–16 \/ 16/);
  assert.deepEqual(app.state(), {...selection, sessionListOpen: true});
});

test('catalog timeouts abort one request and allow the next poll to recover', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage}); await settle();
  let aborted = false;
  app.setCatalog(({signal}) => new Promise((resolve, reject) => signal.addEventListener('abort', () => { aborted = true; reject(new Error('Synthetic abort')); })));
  await app.advance(5000);
  app.document.dispatchEvent({type: 'visibilitychange'});
  app.document.dispatchEvent({type: 'visibilitychange'}); await settle();
  assert.equal(app.requests.filter(url => url === 'timeline/sessions.json').length, 2, 'visible events must not overlap an in-flight catalog request');
  await app.advance(8000);
  assert.equal(aborted, true);
  assert.deepEqual(app.state(), data.selection);
  app.setCatalog(() => ({sessions: [...data.sessions, {id: 'recovered-after-timeout', project: 'test', hasAnalysis: false}]}));
  await app.advance(5000);
  assert.match(app.elements.get('info').textContent, /49/);
  assert.deepEqual(app.state(), data.selection);
});

test('a page restored from browser history resumes catalog polling', async () => {
  const app = browser(); await settle();
  app.window.dispatchEvent({type: 'pagehide'}); await app.advance(10000);
  const count = app.requests.filter(url => url === 'timeline/sessions.json').length;
  app.window.dispatchEvent({type: 'pageshow'}); await settle();
  assert.equal(app.requests.filter(url => url === 'timeline/sessions.json').length, count + 1);
  await app.advance(5000);
  assert.equal(app.requests.filter(url => url === 'timeline/sessions.json').length, count + 2);
});

test('a failed context read cannot erase saved child pages and selection before the next successful load', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage, seed: {}, messages: () => { throw new Error('Synthetic unavailable context'); }});
  await settle();
  assert.equal(app.selected('session')?.dataset.key, data.sessionId);
  assert.deepEqual(app.nodes('topic').map(card=>card.dataset.key), ['unassigned'],
    'without context, saved graph questions have no confirmed topic membership');
  assert.match(app.nodes('topic')[0].title, /순서 미확정/);
  assert.equal(app.nodes('question').length, 0);
  app.window.dispatchEvent({type: 'pagehide'});
  assert.deepEqual(app.state(), data.selection, 'saving during navigation must preserve the unresolved selection and its child pages');
  const returned = browser({data, storage, seed: {}}); await settle();
  assert.equal(returned.selected('session')?.dataset.key, data.sessionId);
  assert.equal(returned.selected('topic')?.dataset.key, data.selection.topic);
  assert.equal(returned.selected('question')?.dataset.key, data.selection.question);
  assert.deepEqual(returned.state(), data.selection);
  returned.elements.get('mind-actions').querySelector('.source-open').click(); await settle();
  assert.equal(returned.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'m12');
});

test('mind-map topics and the Q&A selector share first-question ordering and unknown labels', async () => {
  const data=fixture(),before=clone(data),app=browser({data});await settle();
  // The analysed fixture session is on the fifth session page.
  for(let page=0;page<4;page++) app.pager('next').click();
  app.sessions().find(card=>card.dataset.key===data.sessionId).click();
  const cards=app.nodes('topic');
  assert.equal(cards.length,18);
  assert.equal(cards[0].dataset.key,'topic:topic-12');
  assert.match(cards[0].title,/첫 질문 원문 #1/);
  assert.deepEqual(cards.slice(0,3).map(card=>card.querySelector('.tree-no').textContent),['1','2','3'],'topics are numbered in display order');
  assert.ok(cards.slice(1).every(card=>card.title.includes('순서 미확정')));
  assert.deepEqual(data,before,'topic ordering must only change presentation');
});

test('session cards show a first-request title as text with activity and keep IDs out of visible metadata', async () => {
  const data = fixture();
  Object.assign(data.sessions[0], {title: '<img src=x onerror=unsafe()> 저장 위치를 알려줘', titleSource: 'first_user', lastMessageAt: '2026-10-23T10:30:00.000Z'});
  Object.assign(data.sessions[1], {titleSource: 'analysis'});
  const before = clone(data), app = browser({data}); await settle();
  const card = app.sessions()[0];
  assert.equal(card.querySelector('.name').textContent, data.sessions[0].title);
  assert.equal(card.querySelector('img'), null, 'title content must never become HTML');
  assert.equal(card.querySelector('.session-title-source').textContent, '첫 요청');
  assert.equal(card.querySelector('.session-project').textContent.endsWith('test'), true);
  assert.match(card.querySelector('.mind-meta').textContent, /분석 있음 · 메시지 32개/);
  assert.match(card.querySelector('.mind-activity').textContent, /^최근 /);
  assert.ok(card.title.includes(data.sessions[0].id), 'exact ID remains available in the tooltip');
  assert.ok(!card.textContent.includes(data.sessions[0].id), 'technical ID must not compete with the descriptive title');
  assert.equal(app.sessions()[1].querySelector('.session-title-source'), null, 'analysis titles are not relabeled as first requests');
  assert.deepEqual(data, before, 'display must not mutate titles, graph IDs, evidence or relations');
});

test('a refreshed descriptive session title preserves the selected branch and remains searchable', async () => {
  const data = fixture(), storage = new Map([[stateKey, JSON.stringify(data.selection)]]);
  const app = browser({data, storage}); await settle();
  const topic = app.selected('topic'), question = app.selected('question');
  const updated = data.sessions.map(session => session.id === data.sessionId ? {...session, title: '배포 오류를 확인해줘', titleSource: 'first_user'} : session);
  app.setCatalog(() => ({sessions: updated})); await app.advance(5000);
  assert.equal(app.selected('session').querySelector('.name').textContent, '배포 오류를 확인해줘');
  assert.equal(app.selected('topic'), topic);
  assert.equal(app.selected('question'), question);
  assert.deepEqual(app.state(), data.selection);
  app.elements.get('query').value = '배포 오류';
  app.elements.get('query').dispatchEvent({type: 'input'});
  assert.match(app.elements.get('info').textContent, /1개/);
  assert.equal(app.selected('session').dataset.key, data.sessionId);
  assert.match(app.elements.get('query').placeholder, /가지 검색/);
});

const scopeKey = 'context-graph-session-scope';
function lineageFixture() {
  const data=fixture();
  for(const [index,session] of data.sessions.entries()) {
    Object.assign(session,{sessionKind:index===0?'primary':'subagent',parentSessionId:index===0?null:'session-0',rootSessionId:index===0?null:'session-0',titleSource:index===0?'first_user':'own_request'});
  }
  return data;
}
function change(app,id,value) {
  app.elements.get(id).value=value;app.elements.get(id).dispatchEvent({type:'change'});
}

test('ordinary conversations are the default even with a legacy selection of an auxiliary session',async()=>{
  const data=lineageFixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const app=browser({data,storage});await settle();
  assert.deepEqual(app.sessions().map(card=>card.dataset.key),['session-0']);
  assert.equal(app.elements.get('catalog-kind').value,'primary');
  assert.equal(app.selected('topic'),null);
  assert.match(app.sessions()[0].textContent,/보조 작업 47개/);
  assert.equal(app.elements.get('catalog-parent-filter').hidden,true);
});

test('auxiliary sessions are grouped by the parent conversation with literal own-request titles',async()=>{
  const data=lineageFixture();
  data.sessions[1].sessionKind='primary';data.sessions[1].title='<img src=x onerror=alert(1)> Parent';
  data.sessions[2].parentSessionId='session-1';data.sessions[2].rootSessionId='session-1';
  data.sessions[2].title='<script>own request</script>';
  const before=clone(data),app=browser({data});await settle();
  change(app,'catalog-kind','subagent');
  assert.equal(app.elements.get('catalog-parent-filter').hidden,false);
  change(app,'catalog-parent','session-1');
  assert.deepEqual(app.sessions().map(card=>card.dataset.key),['session-2']);
  assert.equal(app.sessions()[0].querySelector('.session-title-source').textContent,'분기 후 요청');
  assert.equal(app.sessions()[0].querySelector('script'),null);
  assert.equal(app.elements.get('catalog-parent').querySelector('img'),null);
  assert.ok(app.elements.get('catalog-parent').textContent.includes(data.sessions[1].title));
  assert.deepEqual(data,before,'lineage filtering must not mutate graph IDs, relations, or source titles');
});

test('auxiliary scope and parent survive source navigation with every selected branch and page',async()=>{
  const data=lineageFixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)],[scopeKey,JSON.stringify({kind:'subagent',parent:'session-0'})]]);
  const app=browser({data,storage});await settle();
  assert.equal(app.selected('session')?.dataset.key,data.sessionId);
  app.elements.get('mind-actions').querySelector('.source-open').click();await settle();
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'m12');
  const returned=browser({data,storage});await settle();
  assert.equal(returned.elements.get('catalog-kind').value,'subagent');
  assert.equal(returned.elements.get('catalog-parent').value,'session-0');
  assert.deepEqual(returned.state(),data.selection);
  assert.equal(returned.selected('session')?.dataset.key,data.sessionId);
  assert.equal(returned.selected('topic')?.dataset.key,data.selection.topic);
  assert.equal(returned.selected('question')?.dataset.key,data.selection.question);
});

test('new auxiliary sessions update counts without replacing primary branches or resetting pages',async()=>{
  const data=fixture();for(const session of data.sessions)session.sessionKind='primary';
  const storage=new Map([[stateKey,JSON.stringify(data.selection)]]),app=browser({data,storage});await settle();
  const topic=app.selected('topic'),question=app.selected('question');
  app.setCatalog(()=>({sessions:[{id:'new-helper',sessionKind:'subagent',parentSessionId:data.sessionId,rootSessionId:data.sessionId,title:'Synthetic helper',hasAnalysis:false},...data.sessions]}));
  await app.advance(5000);
  assert.equal(app.selected('session')?.dataset.key,data.sessionId);
  assert.equal(app.selected('topic'),topic);assert.equal(app.selected('question'),question);
  assert.deepEqual(app.state(),data.selection);
  assert.match(app.elements.get('catalog-kind').textContent,/보조 작업 · 1개/);
  assert.equal(app.sessions().some(card=>card.dataset.key==='new-helper'),false);
});

test('explicit unknown lineage is separate and remains accessible without automatic reclassification',async()=>{
  const data=lineageFixture();data.sessions[1].sessionKind='unknown';
  const app=browser({data});await settle();
  change(app,'catalog-kind','unknown');
  assert.deepEqual(app.sessions().map(card=>card.dataset.key),['session-1']);
  app.sessions()[0].click();
  app.elements.get('mind-actions').querySelector('button').click();await settle();
  assert.match(app.requests.at(-1),/session=session-1&limit=100/);
});

function topicUserEvidenceFixture() {
  const data=fixture();
  data.context.analysis.structure.find(node=>node.id==='topic-0').childIds=['source-reviewed'];
  data.context.analysis.structure.push({id:'source-reviewed',kind:'item',parentId:'topic-0'});
  data.graph.nodes.push({id:'reviewed-decision',title:'Saved decision',summary:'',project:'test',classification:'decision',
    provenance:[{session_id:data.sessionId,source_node:'source-reviewed',evidence:[]}]});
  data.context.messages.push({id:'raw-reviewed',index:30,role:'user',timestamp:'',hasUserContent:true});
  data.context.analysis.anchors['source-reviewed']=[{messageId:'raw-reviewed',index:30,sourceMessageId:'saved-review-alias'}];
  return data;
}

test('live topic recovery labels user evidence and opens the stored original without adding a question',async()=>{
  const data=topicUserEvidenceFixture(),before=clone(data),app=browser({data,seed:{}});await settle();
  for(let page=0;page<4;page++)app.pager('next').click();
  app.sessions().find(card=>card.dataset.key===data.sessionId).click();
  const topic=app.nodes('topic').find(card=>card.dataset.key==='topic:topic-0');assert.ok(topic);
  assert.match(topic.title,/질문 0개 · 사용자 근거 원문 #31/);
  topic.click();assert.equal(app.nodes('question').length,0);
  const source=app.elements.get('mind-actions').querySelector('.topic-source-open');assert.ok(source);
  assert.equal(source.disabled,false);source.click();await settle();
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'raw-reviewed');
  assert.equal(app.state().topic,'topic:topic-0');
  assert.deepEqual(data,before);
});

test('offline user-evidence order keeps the reference but disables original navigation',async()=>{
  const data=topicUserEvidenceFixture();data.selection.page=0;
  const app=browser({data,protocol:'file:',storage:new Map([[stateKey,JSON.stringify({...data.selection,topic:'topic:topic-0',question:''})]])});await settle();
  assert.match(app.selected('topic').title,/사용자 근거 원문 #31/);
  assert.equal(app.elements.get('mind-actions').querySelector('.topic-source-open').disabled,true);
  assert.equal(app.requests.length,0);
});

function auxiliaryTopicsFixture() {
  const data=topicUserEvidenceFixture(),structure=data.context.analysis.structure;
  structure.find(node=>node.id==='topic-0').displayGroup='auxiliary';
  const other=structure.find(node=>node.id==='topic-1');other.displayGroup='auxiliary';
  for(let index=0;index<9;index++) {
    const source=`aux-source-${index}`,id=`aux-item-${index}`;other.childIds.push(source);
    structure.push({id:source,kind:'item',parentId:other.id});
    data.graph.nodes.push({id,title:`Existing auxiliary ${index}`,summary:'',project:'test',classification:'assistant_statement',
      provenance:[{session_id:data.sessionId,source_node:source,evidence:[]}]});
  }
  return data;
}

async function selectFixtureSession(app,data) {
  for(let page=0;page<4;page++)app.pager('next').click();
  app.sessions().find(card=>card.dataset.key===data.sessionId).click();
}

test('mind map defaults to primary topics and auxiliary mode opens existing items and exact source',async()=>{
  const data=auxiliaryTopicsFixture(),before=clone(data),app=browser({data,seed:{}});await settle();
  await selectFixtureSession(app,data);
  assert.equal(app.nodes('topic').length,16,'auxiliary topics stay folded away');
  const group=app.nodes('auxiliary-group')[0];
  assert.equal(group.querySelector('.tree-count').textContent,'2');
  assert.equal(group.getAttribute('aria-expanded'),'false');
  assert.ok(app.nodes('topic').every(card=>!['topic:topic-0','topic:topic-1'].includes(card.dataset.key)));
  group.click();
  const auxiliary=app.nodes('topic').filter(card=>card.dataset.group==='auxiliary');
  assert.deepEqual(auxiliary.map(card=>card.dataset.key),['topic:topic-0','topic:topic-1']);
  assert.equal(app.nodes('topic').length,18,'primary topics remain beside the opened group');
  auxiliary[0].click();
  assert.equal(app.nodes('auxiliary-item')[0].dataset.nodeId,'reviewed-decision');
  app.nodes('auxiliary-item')[0].click();
  app.elements.get('inspector').querySelector('.source-open').click();await settle();
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'raw-reviewed');
  assert.equal(app.state().topicGroup,'auxiliary');
  assert.deepEqual(data,before);
});

test('auxiliary group, topic and later item page survive a fresh browser with delayed context',async()=>{
  const data=auxiliaryTopicsFixture(),storage=new Map(),app=browser({data,storage});await settle();
  await selectFixtureSession(app,data);app.nodes('auxiliary-group')[0].click();
  app.nodes('topic').find(card=>card.dataset.key==='topic:topic-1').click();
  const expected=clone(app.state());
  assert.equal(app.nodes('auxiliary-item').length,9,'every stored item branches from the opened auxiliary topic');
  assert.equal(app.nodes('auxiliary-item').at(-1).dataset.nodeId,'aux-item-8');
  const restored=browser({data,storage,seed:{}});await settle();
  assert.deepEqual(restored.state(),expected);
  assert.equal(restored.nodes('auxiliary-group')[0].getAttribute('aria-expanded'),'true');
  assert.equal(restored.nodes('auxiliary-item').at(-1).dataset.nodeId,'aux-item-8');
  restored.nodes('auxiliary-group')[0].click();
  assert.equal(restored.state().topicGroup,undefined);
  assert.equal(restored.state().topic,null);
});

test('topic move and restore persist across a fresh page and select the correct destination page',async()=>{
  const data=fixture(),storage=new Map();
  data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const before=clone(data),server=clone(data.context);let version=0;
  const displayEdit=async request=>{
    assert.deepEqual(Object.keys(request).sort(),['group','revision','session','sourceMapSha256','topic']);
    assert.equal(request.revision,String(version).repeat(64));version++;
    const result={saved:true,session:data.sessionId,topic:request.topic,group:request.group,
      auxiliaryTopicIds:request.group==='auxiliary'?[request.topic]:[],primaryTopicIds:request.group==='primary'?[request.topic]:[],
      topicDisplay:{status:'active',sourceMapSha256:'a'.repeat(64),revision:String(version).repeat(64)}};
    server.session.topicDisplay=result.topicDisplay;
    server.analysis.structure.find(node=>node.id===request.topic).displayGroup=request.group;
    return {ok:true,status:200,json:async()=>result};
  };
  const app=browser({data,storage,seed:{},messages:()=>server,displayEdit});await settle();
  await selectFixtureSession(app,data);
  app.nodes('topic').find(card=>card.dataset.key==='topic:topic-17').click();
  assert.equal(app.elements.get('mind-topic-edit').disabled,false);
  app.elements.get('mind-topic-edit').click();await settle();
  assert.equal(app.state().topicGroup,'auxiliary');assert.equal(app.selected('topic').dataset.key,'topic:topic-17');
  assert.equal(app.selected('topic').dataset.group,'auxiliary');assert.equal(app.selected('topic').dataset.parent,'auxiliary');
  assert.match(app.elements.get('mind-topic-edit-status').textContent,/설정이 저장/);
  const restored=browser({data,storage,seed:{},messages:()=>server,displayEdit});await settle();
  assert.equal(restored.elements.get('mind-topic-edit').textContent,'기본 목록으로 복원');
  restored.elements.get('mind-topic-edit').click();await settle();
  assert.equal(restored.state().topicGroup,undefined);
  assert.equal(restored.selected('topic').dataset.key,'topic:topic-17');
  assert.equal(restored.selected('topic').dataset.group,'primary');
  assert.equal(restored.elements.get('mind-topic-edit').textContent,'보조 정보로 이동');
  assert.deepEqual(data,before);
});

test('pending or conflicting saves cannot duplicate requests or change local grouping',async()=>{
  const data=fixture();data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const pending=deferred(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),displayEdit:()=>pending.promise});await settle();
  app.elements.get('mind-topic-edit').click();app.elements.get('mind-topic-edit').click();await settle();
  assert.equal(app.displayRequests.length,1);assert.equal(app.elements.get('mind-topic-edit').disabled,true);
  pending.resolve({ok:false,status:409});await settle();
  assert.equal(app.state().topicGroup,undefined);assert.equal(app.selected('topic').dataset.key,data.selection.topic);
  assert.match(app.elements.get('mind-topic-edit-status').textContent,/새로고침/);
});

test('offline snapshots offer no preference mutation even with saved edit metadata',async()=>{
  const data=fixture();data.selection.page=0;
  data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const app=browser({data,protocol:'file:',storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  assert.equal(app.elements.get('mind-topic-edit').disabled,true);app.elements.get('mind-topic-edit').click();await settle();
  assert.equal(app.elements.get('mind-topic-name').disabled,true);
  assert.equal(app.elements.get('mind-topic-name-save').disabled,true);app.elements.get('mind-topic-name-save').click();await settle();
  assert.equal(app.requests.length,0);
});

test('name drafts survive catalog refresh and saved aliases survive reload without losing question selection',async()=>{
  const data=fixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const before=clone(data),server=clone(data.context);let version=0;
  const displayEdit=async request=>{
    assert.deepEqual(Object.keys(request).sort(),['displayName','revision','session','sourceMapSha256','topic']);
    const node=server.analysis.structure.find(node=>node.id===request.topic);
    if(request.displayName===null)delete node.displayTitle;else node.displayTitle=request.displayName;
    server.session.topicDisplay={status:'active',sourceMapSha256:'a'.repeat(64),revision:String(++version).repeat(64)};
    return {ok:true,status:200,json:async()=>({saved:true,session:data.sessionId,topic:request.topic,operation:'name',displayName:request.displayName,
      displayNames:request.displayName===null?{}:{[request.topic]:request.displayName},auxiliaryTopicIds:[],primaryTopicIds:[],topicDisplay:server.session.topicDisplay})};
  };
  const app=browser({data,storage,seed:{},messages:()=>server,displayEdit});await settle();
  const input=app.elements.get('mind-topic-name'),alias='<img src=x onerror=unsafe()> 표시 이름';
  input.value=alias;input.dispatchEvent({type:'input'});
  app.setCatalog(()=>({sessions:data.sessions.map(s=>({...s,messageCount:s.messageCount+1}))}));await app.advance(5000);
  assert.equal(input.value,alias,'polling must not overwrite the draft');
  app.elements.get('mind-topic-name-save').click();await settle();
  assert.equal(app.selected('topic').querySelector('.name').textContent,alias);
  assert.equal(app.selected('topic').querySelector('img'),null);
  assert.equal(app.selected('question').dataset.nodeId,'q12');assert.deepEqual(app.state(),data.selection);
  assert.equal(app.elements.get('mind-topic-original-name').textContent,'원래 이름: Topic 12');
  const restored=browser({data,storage,seed:{},messages:()=>server,displayEdit});await settle();
  assert.equal(restored.elements.get('mind-topic-name').value,alias);
  assert.equal(restored.selected('question').dataset.nodeId,'q12');
  restored.elements.get('mind-topic-name-reset').click();await settle();
  assert.equal(restored.selected('topic').querySelector('.name').textContent,'Topic 12');
  assert.equal(restored.elements.get('mind-topic-name').value,'Topic 12');
  assert.equal(restored.elements.get('mind-topic-name-reset').disabled,true);
  assert.deepEqual(restored.state(),data.selection);assert.deepEqual(data,before);
});

test('invalid name input cannot issue a save request or alter the displayed topic',async()=>{
  const data=fixture();data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  const app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  for(const value of ['', ' ', 'x'.repeat(121), 'bad\nname']) {
    const input=app.elements.get('mind-topic-name');input.value=value;input.dispatchEvent({type:'input'});
    app.elements.get('mind-topic-name-save').click();await settle();
    assert.equal(app.displayRequests.length,0);assert.match(app.elements.get('mind-topic-edit-status').textContent,/1~120자/);
    assert.equal(app.selected('topic').querySelector('.name').textContent,'Topic 12');
  }
});

function sessionNameFixture() {
  const data=fixture();
  for(const session of data.sessions)session.sessionDisplay={status:'default',revision:'0'.repeat(64)};
  const sessions=clone(data.sessions),context=clone(data.context);let version=0;
  const edit=async request=>{
    assert.deepEqual(Object.keys(request).sort(),['displayName','revision','session']);
    const session=sessions.find(row=>row.id===request.session);assert.equal(request.revision,session.sessionDisplay.revision);
    if(request.displayName===null)delete session.displayTitle;else session.displayTitle=request.displayName;
    session.sessionDisplay={status:request.displayName===null?'default':'active',revision:String(++version).repeat(64)};
    if(context.session.id===session.id)context.session=clone(session);
    return {ok:true,status:200,json:async()=>({saved:true,session:session.id,displayName:request.displayName,sessionDisplay:session.sessionDisplay})};
  };
  return {data,sessions,context,edit};
}

test('session name drafts, save, reload and reset preserve the original title and selected branch',async()=>{
  const {data,sessions,context,edit}=sessionNameFixture(),before=clone(data);
  const storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const options={data,storage,seed:{},catalog:()=>({sessions}),messages:()=>context,sessionEdit:edit};
  const app=browser(options);await settle();
  const input=app.elements.get('mind-session-name'),alias='<img src=x> 읽기용 세션';
  input.value=alias;input.dispatchEvent({type:'input'});
  app.setCatalog(()=>({sessions:sessions.map(row=>({...row,messageCount:row.messageCount+1}))}));await app.advance(5000);
  assert.equal(input.value,alias);
  app.elements.get('mind-session-name-save').click();await settle();
  assert.equal(app.selected('session').querySelector('.name').textContent,alias);
  assert.equal(app.selected('session').querySelector('img'),null);
  assert.equal(app.elements.get('mind-session-original-name').textContent,'원래 이름: '+data.context.session.title);
  assert.deepEqual(app.state(),data.selection);
  const restored=browser(options);await settle();
  assert.equal(restored.elements.get('mind-session-name').value,alias);
  assert.equal(restored.selected('question').dataset.nodeId,'q12');
  restored.elements.get('mind-session-name-reset').click();await settle();
  assert.equal(restored.selected('session').querySelector('.name').textContent,data.context.session.title);
  assert.deepEqual(restored.state(),data.selection);assert.deepEqual(data,before);
});

test('an unanalysed session can be named without an analysis context or source graph change',async()=>{
  const {data,sessions,context,edit}=sessionNameFixture();
  data.sessions[0].hasAnalysis=false;sessions[0].hasAnalysis=false;
  const before=clone(data),app=browser({data,catalog:()=>({sessions}),messages:()=>context,sessionEdit:edit});await settle();
  app.sessions().find(card=>card.dataset.key==='session-0').click();
  assert.equal(app.elements.get('mind-session-name-save').disabled,false);
  app.elements.get('mind-session-name').value='분석 전 세션';app.elements.get('mind-session-name').dispatchEvent({type:'input'});
  app.elements.get('mind-session-name-save').click();await settle();
  assert.equal(app.selected('session').querySelector('.name').textContent,'분석 전 세션');
  assert.equal(app.elements.get('mind-canvas').classList.contains('raw-chronology'),true);assert.equal(app.nodes('topic').length,0);
  assert.equal(app.state().session,'session-0');assert.deepEqual(data,before);
});

test('an older catalogue response cannot undo an acknowledged session name',async()=>{
  const {data,sessions,context,edit}=sessionNameFixture(),pending=deferred();
  const app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),catalog:()=>({sessions}),messages:()=>context,sessionEdit:edit});await settle();
  app.setCatalog(()=>pending.promise);await app.advance(5000);
  app.elements.get('mind-session-name').value='새로 저장된 이름';app.elements.get('mind-session-name').dispatchEvent({type:'input'});
  app.elements.get('mind-session-name-save').click();await settle();
  assert.equal(app.selected('session').querySelector('.name').textContent,'새로 저장된 이름');
  pending.resolve({sessions:clone(data.sessions)});await settle();
  assert.equal(app.selected('session').querySelector('.name').textContent,'새로 저장된 이름');
  app.setCatalog(()=>({sessions}));await app.advance(5000);
  assert.equal(app.selected('session').querySelector('.name').textContent,'새로 저장된 이름');
});

test('renaming a session found by its old alias keeps it selected and retains original-title search',async()=>{
  const {data,sessions,context,edit}=sessionNameFixture();
  sessions[36].displayTitle='Old alias';context.session=clone(sessions[36]);
  const selection={...data.selection,page:0,filters:{query:'Old alias',project:''}};
  const app=browser({data,storage:new Map([[stateKey,JSON.stringify(selection)]]),catalog:()=>({sessions}),messages:()=>context,sessionEdit:edit});await settle();
  app.elements.get('mind-session-name').value='New alias';app.elements.get('mind-session-name').dispatchEvent({type:'input'});
  app.elements.get('mind-session-name-save').click();await settle();
  assert.equal(app.state().session,data.sessionId);assert.equal(app.selected('question').dataset.nodeId,'q12');
  assert.equal(app.elements.get('query').value,'');assert.equal(app.state().page,4);
  app.elements.get('query').value=data.context.session.title;app.elements.get('query').dispatchEvent({type:'input'});
  assert.match(app.elements.get('info').textContent,/1개/);assert.equal(app.selected('session').querySelector('.name').textContent,'New alias');
});

function answerEvidenceFixture() {
  const data=fixture(),question=data.graph.nodes.find(node=>node.id==='q12');
  question.classification='answered_question';question.status='answered';
  data.context.messages.find(message=>message.id==='m12').index=36;
  data.context.analysis.anchors['source-q12'][0].index=36;
  data.context.messages.push({id:'raw-answer-39',index:38,role:'assistant',timestamp:'2026-10-23T00:01:00.000Z'});
  data.context.analysis.anchors['source-q12'].push({messageId:'raw-answer-39',index:38,sourceMessageId:'m39'});
  data.context.analysis.assistantEvidence={'source-q12':[{messageId:'raw-answer-39',index:38,sourceMessageId:'m39',quotes:['<img src=x onerror=alert(1)> Stored answer excerpt'],evidenceCount:1}]};
  return data;
}

function itemTitleFixture() {
  const data=answerEvidenceFixture();
  data.context.session.topicDisplay={status:'default',sourceMapSha256:'a'.repeat(64),revision:'0'.repeat(64)};
  data.graph.nodes.push({id:'stored-answer',title:'Stored answer title',summary:'',project:'test',classification:'assistant_statement',
    provenance:[{session_id:data.sessionId,source_node:'source-answer',evidence:[]}]});
  data.graph.edges.push({id:'stored-answer-edge',from:'stored-answer',to:'q12',type:'answers',sessions:[data.sessionId]});
  data.context.analysis.structure.find(node=>node.id==='topic-12').childIds.push('source-answer');
  data.context.analysis.structure.push({id:'source-answer',kind:'item',parentId:'topic-12'});
  data.context.analysis.relations.push({id:'original-answer-edge',from:'source-answer',to:'source-q12',type:'answers'});
  data.context.messages.push({id:'raw-linked-answer',index:40,role:'assistant',timestamp:''});
  data.context.analysis.anchors['source-answer']=[{messageId:'raw-linked-answer',index:40,sourceMessageId:'stored-answer-message'}];
  data.context.analysis.itemLabels=Object.fromEntries(data.graph.nodes.map(node=>[node.provenance[0].source_node,{title:node.title}]));
  data.context.analysis.evidenceTitles={};
  const server=clone(data.context);let version=0;
  const itemEdit=async request=>{
    assert.equal(request.session,data.sessionId);assert.equal(request.revision,server.session.topicDisplay.revision);
    assert.ok(!Object.hasOwn(request,'quote')&&!Object.hasOwn(request,'content'));
    if(request.evidenceMessage) {
      const titles=server.analysis.evidenceTitles[request.item] ||= {};
      if(request.displayTitle===null)delete titles[request.evidenceMessage];else titles[request.evidenceMessage]=request.displayTitle;
      if(!Object.keys(titles).length)delete server.analysis.evidenceTitles[request.item];
    } else {
      const label=server.analysis.itemLabels[request.item];
      if(request.displayTitle===null)delete label.displayTitle;else label.displayTitle=request.displayTitle;
    }
    server.session.topicDisplay={status:'active',sourceMapSha256:'a'.repeat(64),revision:String(++version).repeat(64)};
    return {ok:true,status:200,json:async()=>({saved:true,session:data.sessionId,item:request.item,evidenceMessage:request.evidenceMessage || null,
      displayTitle:request.displayTitle,auxiliaryTopicIds:[],primaryTopicIds:[],displayNames:{},
      itemLabels:clone(server.analysis.itemLabels),evidenceTitles:clone(server.analysis.evidenceTitles),topicDisplay:server.session.topicDisplay})};
  };
  return {data,server,itemEdit};
}

test('question display titles save, survive reload and restore without changing source titles or selection',async()=>{
  const {data,server,itemEdit}=itemTitleFixture(),before=clone(data),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const options={data,storage,seed:{},messages:()=>server,itemEdit},app=browser(options);await settle();
  app.elements.get('mind-question-title-edit').click();assert.equal(app.elements.get('item-title-dialog').open,true);
  const alias='<img src=x> 읽기 쉬운 질문';
  app.elements.get('item-title-input').value=alias;app.elements.get('item-title-input').dispatchEvent({type:'input'});
  app.elements.get('item-title-save').click();await settle();
  assert.equal(app.selected('question').querySelector('.name').textContent,alias);
  assert.equal(app.selected('question').querySelector('img'),null);
  assert.equal(app.elements.get('item-title-original').textContent,'원래 제목: Question 12');
  assert.equal(app.itemRequests[0].item,'source-q12');assert.deepEqual(app.state(),data.selection);
  const reloaded=browser(options);await settle();
  assert.equal(reloaded.selected('question').querySelector('.name').textContent,alias);
  reloaded.elements.get('mind-question-title-edit').click();reloaded.elements.get('item-title-reset').click();await settle();
  assert.equal(reloaded.selected('question').querySelector('.name').textContent,'Question 12');
  assert.deepEqual(data,before);assert.deepEqual(reloaded.state(),data.selection);
});

test('linked answers and quote-evidence titles stay distinct and keep their original source navigation',async()=>{
  const {data,server,itemEdit}=itemTitleFixture(),before=clone(data),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),messages:()=>server,itemEdit});await settle();
  assert.equal(app.elements.get('mind-answer-title-action').hidden,true,'no answer leaf is chosen yet');
  app.nodes('answer').find(card=>card.dataset.nodeId==='stored-answer').click();await settle();
  assert.equal(app.elements.get('mind-answer-title-action').hidden,false);
  app.elements.get('mind-answer-title-edit').click();
  app.elements.get('item-title-input').value='연결 답변 표시 제목';app.elements.get('item-title-input').dispatchEvent({type:'input'});
  app.elements.get('item-title-save').click();await settle();app.elements.get('item-title-close').click();
  const answer=app.answers().find(card=>card.dataset.nodeId==='stored-answer');
  assert.equal(answer.querySelector('.name').textContent,'연결 답변 표시 제목');assert.equal(answer.dataset.edgeIds,'stored-answer-edge');
  assert.equal(answer.getAttribute('aria-pressed'),'true');
  app.answers().find(card=>card.dataset.kind==='answer-evidence').click();await settle();
  app.elements.get('mind-answer-title-edit').click();
  app.elements.get('item-title-input').value='답변 근거 표시 제목';app.elements.get('item-title-input').dispatchEvent({type:'input'});
  app.elements.get('item-title-save').click();await settle();app.elements.get('item-title-close').click();
  const evidence=app.answers().find(card=>card.dataset.kind==='answer-evidence');
  assert.equal(evidence.querySelector('.name').textContent,'답변 근거 표시 제목');
  assert.match(evidence.title,/어시스턴트 원문 #39/,'the original reference stays in the tooltip');
  assert.equal(app.elements.get('mind-actions').querySelector('.answer-evidence-preview').textContent,data.context.analysis.assistantEvidence['source-q12'][0].quotes[0]);
  assert.equal(evidence.dataset.nodeId,undefined);assert.equal(evidence.dataset.messageId,'raw-answer-39');
  evidence.click();await settle();assert.equal(app.elements.get('source-reader').hidden,false);
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'raw-answer-39');
  assert.equal(app.itemRequests[0].item,'source-answer');assert.equal(app.itemRequests[1].item,'source-q12');
  assert.equal(app.itemRequests[1].evidenceMessage,'raw-answer-39');assert.deepEqual(data,before);
});

test('conflicting item edits and offline snapshots do not alter displayed titles',async()=>{
  const {data}=itemTitleFixture(),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const app=browser({data,storage,itemEdit:async()=>({ok:false,status:409})});await settle();
  app.elements.get('mind-question-title-edit').click();app.elements.get('item-title-input').value='Not saved';
  app.elements.get('item-title-input').dispatchEvent({type:'input'});app.elements.get('item-title-save').click();await settle();
  assert.match(app.elements.get('item-title-status').textContent,/새로고침/);
  assert.equal(app.selected('question').querySelector('.name').textContent,'Question 12');
  data.selection.page=0;
  const offline=browser({data,protocol:'file:',storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  offline.elements.get('mind-question-title-edit').click();assert.equal(offline.elements.get('item-title-save').disabled,true);
  offline.elements.get('item-title-save').click();await settle();assert.equal(offline.requests.length,0);
});

test('an item editor opened during source loading becomes editable when the context arrives',async()=>{
  const {data,server,itemEdit}=itemTitleFixture(),pending=deferred();
  const app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),messages:()=>pending.promise,itemEdit});await settle();
  app.elements.get('mind-question-title-edit').click();assert.equal(app.elements.get('item-title-save').disabled,true);
  pending.resolve({...server,total:server.messages.length,offset:0,revision:'ready'});await settle();
  assert.equal(app.elements.get('item-title-dialog').open,true);assert.equal(app.elements.get('item-title-save').disabled,false);
});

test('a merged graph node keeps display titles separate for each original question occurrence',async()=>{
  const {data,server,itemEdit}=itemTitleFixture();
  data.graph.nodes.find(node=>node.id==='q12').provenance.push({session_id:data.sessionId,source_node:'source-alternate',evidence:[]});
  for(const context of [data.context,server]) {
    context.analysis.structure.find(node=>node.id==='topic-12').childIds.push('source-alternate');
    context.analysis.structure.push({id:'source-alternate',kind:'item',parentId:'topic-12'});
    context.analysis.anchors['source-alternate']=[{messageId:'m5',index:5,sourceMessageId:'alternate-message'}];
    context.analysis.itemLabels['source-alternate']={title:'Other original question'};
  }
  const before=clone(data),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]]),messages:()=>server,itemEdit});await settle();
  app.elements.get('mind-question-title-edit').click();app.elements.get('item-title-input').value='Only the selected occurrence';
  app.elements.get('item-title-input').dispatchEvent({type:'input'});app.elements.get('item-title-save').click();await settle();
  assert.equal(app.selected('question').querySelector('.name').textContent,'Only the selected occurrence');
  app.elements.get('item-title-close').click();
  assert.equal(app.nodes('question').length,17,'both occurrences of the merged node branch from the topic');
  const alternate=app.nodes('question').find(card=>card.dataset.sourceNode==='source-alternate');
  assert.equal(alternate.dataset.nodeId,'q12');assert.equal(alternate.querySelector('.name').textContent,'Other original question');
  assert.deepEqual(data,before);
});

test('mind map shows stored answer evidence without an answers edge and opens exact assistant message 39',async()=>{
  const data=answerEvidenceFixture(),before=clone(data),storage=new Map([[stateKey,JSON.stringify(data.selection)]]);
  const app=browser({data,storage});await settle();
  assert.equal(app.selected('question')?.dataset.nodeId,'q12');
  assert.match(app.selected('question').title,/원문 #37 · 답변 연결 0개 · 답변 근거 1개/);
  const cards=app.answers();assert.equal(cards.length,1);
  assert.equal(cards[0].dataset.kind,'answer-evidence');
  assert.equal(cards[0].dataset.messageId,'raw-answer-39');
  assert.equal(cards[0].dataset.parent,data.selection.question,'evidence hangs from its question');
  assert.match(cards[0].textContent,/어시스턴트 원문 #39/);
  assert.equal(cards[0].querySelector('img'),null);
  app.draw();
  assert.equal(app.edges().find(edge=>edge.dataset.key===cards[0].dataset.key)?.getAttribute('class'),'mind-edge mind-edge-active mind-evidence-edge','stored evidence draws a dotted branch');
  cards[0].click();await settle();
  assert.equal(app.elements.get('source-reader-body').querySelector('.reader-target').dataset.messageId,'raw-answer-39');
  const preview=app.elements.get('mind-actions').querySelector('.answer-evidence-preview');
  assert.equal(preview.textContent,data.context.analysis.assistantEvidence['source-q12'][0].quotes[0]);
  assert.equal(preview.querySelector('img'),null);
  assert.deepEqual(app.state(),{...data.selection,answer:cards[0].dataset.key});
  assert.deepEqual(data,before,'virtual evidence cards must not add graph nodes/edges or alter evidence');
});

test('branches connect each parent to its visible children and only a newly opened branch animates',async()=>{
  const data=fixture(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();app.draw();
  const edges=app.edges();
  assert.equal(edges.length,18+16+1,'root to topics, topic to questions and the empty-answer note');
  assert.equal(edges.filter(edge=>edge.getAttribute('class').includes('mind-edge-reveal')).length,0,'a restored tree does not replay the reveal');
  assert.equal(edges.filter(edge=>edge.dataset.parent===data.selection.topic).length,16);
  assert.ok(edges.find(edge=>edge.dataset.key===data.selection.question).getAttribute('class').includes('mind-edge-active'));
  assert.ok(edges.find(edge=>edge.dataset.key===data.selection.topic).getAttribute('class').includes('mind-edge-active'));
  assert.ok(!edges.find(edge=>edge.dataset.key==='topic:topic-3').getAttribute('class').includes('mind-edge-active'));
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-3').click();app.draw();
  assert.equal(app.nodes('question').length,0,'opening another topic folds the previous branch');
  assert.equal(app.state().topic,'topic:topic-3');assert.equal(app.state().question,'');
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-12').click();app.draw();
  const questions=app.nodes('question');
  assert.equal(questions.length,16);
  assert.ok(questions.every(node=>node.classList.contains('mind-node-reveal')));
  assert.deepEqual(questions.slice(0,2).map(node=>node.style['--mind-delay']),['130ms','168ms'],'children unfold in sequence after their lines');
  assert.deepEqual(questions.slice(0,3).map(node=>node.querySelector('.tree-no').textContent),['1.1','1.2','1.3']);
  assert.equal(app.edges().filter(edge=>edge.getAttribute('class').includes('mind-edge-reveal')).length,16);
  assert.ok(app.nodes('topic').every(node=>!node.classList.contains('mind-node-reveal')),'existing branches stay still');
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-12').click();app.draw();
  assert.equal(app.nodes('question').length,0,'clicking the open topic folds it');
  assert.equal(app.state().topic,null);
  app.elements.get('mind-reduce-motion').checked=true;app.elements.get('mind-reduce-motion').dispatchEvent({type:'change'});
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-12').click();app.draw();
  assert.ok(app.nodes('question').every(node=>!node.classList.contains('mind-node-reveal')),'reduced motion skips the reveal');
});

test('a legacy page array restores as the session-list page',async()=>{
  const data=fixture(),storage=new Map([[stateKey,JSON.stringify({...data.selection,page:undefined,pages:[3,0,0,0],sessionListOpen:true})]]);
  const app=browser({data,storage});await settle();
  assert.match(app.elements.get('mind-sessions').textContent,/25–32 \/ 48/);
  app.window.dispatchEvent({type:'pagehide'});
  assert.equal(app.state().page,3);assert.equal(app.state().pages,undefined,'the saved shape moves to the single page number');
});

const cameraOf=app=>{const match=app.elements.get('mind-tree').style.transform.match(/translate\((-?[\d.]+)px,(-?[\d.]+)px\) scale\(([\d.]+)\)/);return match && {x:Number(match[1]),y:Number(match[2]),k:Number(match[3])};};

test('a session opens fitted around its root; branches pan the camera and the buttons change only the scale',async()=>{
  const data=fixture(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  const first=cameraOf(app);assert.ok(first,'the tree carries a transform');
  assert.ok(first.k>0 && first.k<=1);assert.match(app.elements.get('mind-zoom-level').textContent,/^\d+%$/);
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-3').click();
  const opened=cameraOf(app);assert.equal(opened.k,first.k,'opening a branch pans without rescaling');
  app.elements.get('mind-zoom-in').click();assert.ok(cameraOf(app).k>opened.k);
  app.elements.get('mind-zoom-out').click();assert.ok(Math.abs(cameraOf(app).k-opened.k)<1e-9);
  app.elements.get('mind-zoom-reset').click();assert.equal(cameraOf(app).k,1);
  app.elements.get('mind-fit').click();assert.ok(cameraOf(app).k<1,'the whole tree is taller than the box, so fitting shrinks it');
  assert.equal(app.elements.get('mind-zoom-level').textContent,Math.round(cameraOf(app).k*100)+'%');
  const before=cameraOf(app);app.openList();app.sessions().find(card=>card.dataset.key==='session-37').click();
  assert.notDeepEqual(cameraOf(app),before,'another session is fitted afresh');
  assert.equal(app.state().session,'session-37');
});

test('dragging and wheel gestures move or scale the tree without touching the saved selection',async()=>{
  const data=fixture(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  const host=app.elements.get('mind-tree-host'),start=cameraOf(app);
  host.dispatchEvent({type:'pointerdown',pointerId:1,button:0,clientX:100,clientY:100});
  host.dispatchEvent({type:'pointermove',pointerId:1,clientX:160,clientY:130});
  assert.equal(host.classList.contains('mind-dragging'),true);
  host.dispatchEvent({type:'pointerup',pointerId:1,clientX:160,clientY:130});
  const moved=cameraOf(app);
  assert.equal(moved.x-start.x,60);assert.equal(moved.y-start.y,30);assert.equal(moved.k,start.k);
  assert.equal(host.classList.contains('mind-dragging'),false);
  host.dispatchEvent({type:'wheel',deltaX:0,deltaY:-100,ctrlKey:true,clientX:120,clientY:80,preventDefault(){}});
  assert.ok(cameraOf(app).k>moved.k,'ctrl+wheel zooms in');
  const zoomed=cameraOf(app);
  host.dispatchEvent({type:'wheel',deltaX:10,deltaY:20,ctrlKey:false,clientX:120,clientY:80,preventDefault(){}});
  assert.equal(cameraOf(app).x,zoomed.x-10);assert.equal(cameraOf(app).y,zoomed.y-20);assert.equal(cameraOf(app).k,zoomed.k,'a plain wheel pans');
  host.dispatchEvent({type:'pointerdown',pointerId:2,button:0,clientX:50,clientY:50});
  host.dispatchEvent({type:'pointermove',pointerId:2,clientX:51,clientY:51});
  host.dispatchEvent({type:'pointerup',pointerId:2,clientX:51,clientY:51});
  assert.equal(host.classList.contains('mind-dragging'),false,'a tiny movement is a tap, not a drag');
  const panned=cameraOf(app);host.scrollLeft=10;host.scrollTop=40;host.dispatchEvent({type:'scroll'});
  assert.equal(host.scrollTop,0);assert.equal(host.scrollLeft,0);
  assert.deepEqual(cameraOf(app),{x:panned.x-10,y:panned.y-40,k:panned.k},'a browser scroll of the clipped box becomes a camera move');
  assert.deepEqual(app.state(),data.selection);
});

test('arrow keys walk the branches, open and fold them, and move the camera to the focused node',async()=>{
  const data=fixture(),app=browser({data,storage:new Map([[stateKey,JSON.stringify({...data.selection,question:''})]])});await settle();
  const tree=app.elements.get('mind-tree'),press=(key)=>tree.dispatchEvent({type:'keydown',key,target:app.document.activeElement,preventDefault(){}});
  const focused=()=>app.document.activeElement.dataset;
  app.selected('topic').focus();
  press('ArrowRight');assert.equal(focused().kind,'question');assert.equal(focused().key,JSON.stringify([data.sessionId,'source-q0','q0']),'→ enters the open branch');
  const before=cameraOf(app);press('ArrowDown');assert.equal(focused().key,JSON.stringify([data.sessionId,'source-q1','q1']));
  press('End');assert.equal(focused().key,'topic:topic-17','End reaches the last visible branch');
  assert.notDeepEqual(cameraOf(app),before,'the camera follows the focused row');
  press('ArrowUp');assert.equal(focused().key,'topic:topic-16');
  press('Home');assert.equal(focused().kind,'session');
  press('ArrowRight');assert.equal(focused().key,'topic:topic-12','→ on the root enters its first branch');
  press('ArrowRight');assert.equal(focused().key,JSON.stringify([data.sessionId,'source-q0','q0']));
  press('ArrowLeft');assert.equal(focused().key,'topic:topic-12','← steps back to the parent');
  press('ArrowLeft');assert.equal(app.nodes('question').length,0,'← on an open branch folds it');
  assert.equal(app.state().topic,null);assert.equal(focused().key,'topic:topic-12','focus stays on the folded branch');
  press('ArrowDown');assert.equal(focused().key,'topic:topic-0');
  press('ArrowRight');assert.equal(app.state().topic,'topic:topic-0','→ on a folded branch opens it');
  assert.equal(focused().key,'topic:topic-0');
  press('ArrowUp');assert.equal(focused().key,'topic:topic-12');
  press('Home');assert.equal(focused().kind,'session');
  press('ArrowLeft');assert.equal(focused().kind,'session','the root has no parent to step to');
  assert.equal(app.requests.filter(url=>url.startsWith('timeline/context.json')).length,1,'keyboard walking fetches nothing new');
});

test('the search box lights matching branches of the open session instead of hiding it',async()=>{
  const data=fixture(),app=browser({data,storage:new Map([[stateKey,JSON.stringify(data.selection)]])});await settle();
  app.elements.get('query').value='Question 1';app.elements.get('query').dispatchEvent({type:'input'});
  assert.equal(app.state().session,data.sessionId,'the open session stays selected');
  assert.equal(app.elements.get('mind-tree').classList.contains('searching'),true);
  const hits=app.nodes('question').filter(node=>node.classList.contains('mind-hit')).map(node=>node.querySelector('.name').textContent);
  assert.deepEqual(hits,['Question 1','Question 10','Question 11','Question 12','Question 13','Question 14','Question 15']);
  assert.equal(app.nodes('topic').filter(node=>node.classList.contains('mind-hit')).length,0,'topic labels themselves do not match');
  assert.match(app.elements.get('mind-selection').textContent,/맞는 가지 7개/);
  app.nodes('topic').find(node=>node.dataset.key==='topic:topic-12').click();
  const folded=app.nodes('topic').find(node=>node.dataset.key==='topic:topic-12');
  assert.equal(folded.querySelector('.tree-hits').textContent,'검색 7','a folded branch counts the matches inside it');
  assert.equal(folded.classList.contains('mind-hit'),true);
  assert.match(app.elements.get('info').textContent,/0개/,'the session list is still narrowed by the term');
  app.openList();assert.equal(app.sessions().length,0);
  app.elements.get('reset').click();
  assert.equal(app.elements.get('mind-tree').classList.contains('searching'),false);
  assert.equal(app.state().session,data.sessionId);
});

test('offline answer evidence offers existing question details without raw quotation or network requests',async()=>{
  const data=answerEvidenceFixture();delete data.context.analysis.assistantEvidence['source-q12'][0].quotes;
  data.selection.page=0;
  const storage=new Map([[stateKey,JSON.stringify(data.selection)]]),app=browser({data,storage,protocol:'file:'});await settle();
  const card=app.answers()[0];assert.ok(card);
  assert.equal(card.dataset.kind,'answer-evidence');
  assert.ok(!card.title.includes('<img'),'offline evidence carries no quotation');
  card.click();await settle();
  assert.equal(app.elements.get('detail-panel').hidden,false);
  assert.deepEqual(app.requests,[]);
});
