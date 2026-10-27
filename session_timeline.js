(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const itemTitle=node=>data?.analysis.itemLabels?.[node.id]?.displayTitle || node.title;
  const sessionTitle=session=>session.displayTitle || session.title;
  const labels = JSON.parse(byId("timeline-labels").textContent);
  const messages = byId("message-list");
  const detail = byId("analysis-detail");
  const follow = byId("follow-latest");
  const overview = byId("analysis-overview");
  const number = new Intl.NumberFormat("ko-KR");
  const date = new Intl.DateTimeFormat("ko-KR", {dateStyle: "short", timeStyle: "medium"});
  let sessions = [], data = null, selected = new URLSearchParams(location.hash.slice(1)).get("session") || "";
  let selectedNode = "", unplacedMode = false, requestSerial = 0, statusKey = "";
  let jumpMessage = new URLSearchParams(location.hash.slice(1)).get("message") || "";
  let view = "conversation", category = "all";
  const readingKey="session-atlas-reader-state-v1";
  function savedReading(sid){try{const raw=localStorage.getItem(readingKey);if(raw?.length>100000)return null;const rows=JSON.parse(raw||"[]");return Array.isArray(rows)?rows.find(r=>r.session===sid && Number.isInteger(r.offset) && r.offset>=0):null;}catch(_){return null;}}
  let restoreReading=!jumpMessage?savedReading(selected):null;
  follow.checked=restoreReading?Boolean(restoreReading.follow):!jumpMessage;
  function persistReading(){
    if(!data || data.session.id!==selected)return;
    const viewport=messages.getBoundingClientRect(),rows=[...messages.querySelectorAll('.message')];
    const visible=rows.find(e=>e.getBoundingClientRect().bottom>viewport.top+2) || rows[0];
    const entry={session:selected,offset:data.offset,follow:follow.checked,message:visible?.dataset.messageId||"",
      within:visible?Math.max(0,viewport.top-visible.getBoundingClientRect().top):0,
      opened:[...messages.querySelectorAll('.raw-message[open]')].map(e=>e.dataset.stateKey.split(':')[0]).slice(0,100)};
    try{const old=JSON.parse(localStorage.getItem(readingKey)||"[]");localStorage.setItem(readingKey,JSON.stringify([entry,...(Array.isArray(old)?old:[]).filter(r=>r.session!==selected)].slice(0,100)));}catch(_){}
  }
  let searchPage = null, searchArguments = null, searchSerial = 0, highlightTerm = "", locatedMessageId = "";
  const searchDialog = byId("search-dialog");
  let sessionKindFilter="primary", sessionParentFilter="";
  try {const scope=JSON.parse(sessionStorage.getItem("context-graph-timeline-scope") || "{}");if(["primary","subagent","unknown"].includes(scope.kind)) {sessionKindFilter=scope.kind;sessionParentFilter=typeof scope.parent==="string"?scope.parent:"";}} catch (_) {}
  const sessionKind=session=>["primary","subagent","unknown"].includes(session.sessionKind)?session.sessionKind:"primary";
  const parentKey=session=>[session.groupSessionId,session.rootSessionId,session.parentSessionId].find(id=>id && id!==session.id) || "unknown-parent";
  function rememberScope() {
    try {sessionStorage.setItem("context-graph-timeline-scope",JSON.stringify({kind:sessionKindFilter,parent:sessionParentFilter}));} catch (_) {}
  }
  function revealSession(sid) {
    const session=sessions.find(item=>item.id===sid);
    if(!session) return;
    sessionKindFilter=sessionKind(session);
    if(sessionKindFilter==="subagent") sessionParentFilter=parentKey(session);
    rememberScope();
  }
  function scopeControls() {
    const labels={primary:"일반 세션",subagent:"보조 작업",unknown:"분류 미확정"};
    function options(control,choices,value) {
      const signature=JSON.stringify(choices);
      if(control.dataset.options!==signature) {
        control.replaceChildren();
        for(const [key,title] of choices) {const option=el("option",title);option.value=key;control.append(option);}
        control.dataset.options=signature;
      }
      control.value=value;
    }
    options(byId("session-kind"),Object.entries(labels).map(([key,title])=>[key,`${title} · ${sessions.filter(item=>sessionKind(item)===key).length}개`]),sessionKindFilter);
    const groups=new Map();
    for(const session of sessions.filter(item=>sessionKind(item)==="subagent")) {const key=parentKey(session);groups.set(key,(groups.get(key)||0)+1);}
    if(!groups.has(sessionParentFilter)) sessionParentFilter=groups.keys().next().value || "";
    options(byId("session-parent"),[...groups].map(([key,count])=>{
      const parent=sessions.find(item=>item.id===key);
      return [key,`${parent?.displayTitle || parent?.title || (key==="unknown-parent"?"상위 세션 미확정":"수집되지 않은 상위 세션 · "+key.slice(0,8))} · ${count}개`];
    }),sessionParentFilter);
    byId("session-parent").disabled=!groups.size;
    byId("session-parent-filter").hidden=sessionKindFilter!=="subagent";
  }

  function appendHighlight(container, text, term) {
    if (!term) { container.textContent = text; return; }
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const expression = new RegExp(escaped, "giu");
    let end = 0, count = 0;
    for (const match of text.matchAll(expression)) {
      if (++count > 200) break;
      container.append(document.createTextNode(text.slice(end, match.index)), el("mark", match[0]));
      end = match.index + match[0].length;
    }
    container.append(document.createTextNode(text.slice(end)));
  }
  async function runSearch(offset = 0) {
    const serial = ++searchSerial;
    searchPage = null;
    byId("search-results").replaceChildren();
    byId("search-previous").disabled = true;
    byId("search-next").disabled = true;
    byId("search-summary").textContent = "원문을 검색하고 있습니다…";
    try {
      const response = await fetch("timeline/search.json", {method:"POST", cache:"no-store", headers:{"Content-Type":"application/json"}, body:JSON.stringify({...searchArguments, offset, limit:30})});
      if (!response.ok) throw new Error("Search unavailable");
      const result = await response.json();
      if (serial !== searchSerial) return;
      searchPage = result;
      byId("search-summary").textContent = result.total ? `${number.format(result.total)}개 메시지 중 ${offset+1}–${offset+result.results.length} · 최근 세션 순` : "검색 결과가 없습니다. 다른 단어나 검색 범위를 확인하세요.";
      const fragment = document.createDocumentFragment();
      for (const hit of result.results) {
        const button = el("button", "", "search-result");
        button.type = "button";
        button.append(el("strong", hit.sessionTitle), el("span", `${hit.project} · ${hit.role === "user" ? "사용자" : "어시스턴트"} · 메시지 #${hit.index+1} · ${time(hit.timestamp)}`, "muted"));
        const snippet = el("span", "", "search-snippet");
        snippet.append(document.createTextNode(hit.snippet.slice(0,hit.matchStart)), el("mark", hit.snippet.slice(hit.matchStart,hit.matchEnd)), document.createTextNode(hit.snippet.slice(hit.matchEnd)));
        button.append(snippet, el("span", "이 메시지와 앞뒤 대화 읽기 →", "search-open"));
        button.addEventListener("click", async () => {
          searchDialog.close();
          highlightTerm = searchArguments.query;
          byId("session-search").value = "";
          byId("session-project").value = "";
          byId("analysed-only").checked = false;
          try { await choose(hit.sessionId, hit.id); } catch (_) { showError(); }
        });
        fragment.append(button);
      }
      byId("search-results").replaceChildren(fragment);
      byId("search-previous").disabled = offset === 0;
      byId("search-next").disabled = offset + result.results.length >= result.total;
      byId("reopen-search").hidden = false;
    } catch (_) {
      if (serial === searchSerial) byId("search-summary").textContent = "검색하지 못했습니다. 수집기 연결을 확인한 후 다시 검색하세요.";
    }
  }

  function group(node) {
    if (["decision", "superseded_decision"].includes(node.classification)) return "decision";
    if (["open_question", "answered_question"].includes(node.classification)) return "question";
    if (["action_item", "completed_action"].includes(node.classification)) return "action";
    if (["suggestion", "rejected_proposal"].includes(node.classification)) return "proposal";
    return "other";
  }
  function highlightSelection() {
    for (const button of document.querySelectorAll('button[data-analysis-id]')) {
      button.setAttribute("aria-pressed", String(button.dataset.analysisId === selectedNode));
    }
  }
  function openDetail() {
    byId("analysis-panel").hidden = false;
    document.body.classList.add("has-detail");
  }
  function closeDetail() {
    selectedNode = "";
    unplacedMode = false;
    byId("analysis-panel").hidden = true;
    document.body.classList.remove("has-detail");
    highlightSelection();
  }
  function updateViewSummary() {
    byId("message-list").hidden = view !== "conversation";
    overview.hidden = view !== "analysis";
    byId("message-paging").hidden = view !== "conversation";
    byId("analysis-filters").hidden = view !== "analysis";
    byId("view-conversation").setAttribute("aria-pressed", String(view === "conversation"));
    byId("view-analysis").setAttribute("aria-pressed", String(view === "analysis"));
    byId("view-analysis").textContent = `정리된 내용${data ? " · " + number.format(data.analysis.nodes.length) : ""}`;
    if (!data) return;
    const waiting = data.session.pendingMessageCount;
    byId("timeline-summary").textContent = view === "analysis" ?
      `이 세션의 저장된 분석 ${number.format(data.analysis.nodes.length)}개 · 항목을 선택하면 원문 근거를 볼 수 있습니다.` :
      `${data.total ? data.offset + 1 : 0}–${data.offset + data.messages.length} / ${number.format(data.total)}개 메시지` +
      (waiting === null ? " · 분석 범위 미확정" : waiting ? ` · 새 메시지 ${number.format(waiting)}개 분석 대기` : "");
  }
  function renderOverview() {
    if (!data) { overview.replaceChildren(); return; }
    const oldScroll = overview.scrollTop;
    const names = {all:"전체", decision:"결정", question:"질문", action:"작업", proposal:"제안", other:"그 외"};
    const filters = document.createDocumentFragment();
    for (const [key, name] of Object.entries(names)) {
      const count = key === "all" ? data.analysis.nodes.length : data.analysis.nodes.filter(node => group(node) === key).length;
      if (!count && key !== "all") continue;
      const button = el("button", `${name} ${number.format(count)}`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(category === key));
      button.addEventListener("click", () => { category = key; overview.scrollTop = 0; renderOverview(); });
      filters.append(button);
    }
    byId("analysis-filters").replaceChildren(filters);
    const sorted = data.analysis.nodes.filter(node => category === "all" || group(node) === category).slice().sort((a,b) =>
      (data.analysis.anchors[a.id]?.[0]?.index ?? Infinity) - (data.analysis.anchors[b.id]?.[0]?.index ?? Infinity));
    const fragment = document.createDocumentFragment();
    for (const node of sorted) {
      const card = analysisButton(node.id);
      const anchors = data.analysis.anchors[node.id] || [];
      card.append(el("span", anchors.length ? `근거 메시지 #${anchors[0].index + 1}${anchors.length > 1 ? " 외 " + (anchors.length-1) + "개" : ""}` : "근거 시점 미확정", "analysis-source"));
      fragment.append(card);
    }
    if (!sorted.length) fragment.append(el("p", data.session.hasAnalysis ? "이 분류의 분석 항목이 없습니다." : "아직 저장된 분석이 없습니다. 분석을 요청하면 결정·질문·작업이 여기에 모입니다.", "empty"));
    overview.replaceChildren(fragment);
    overview.scrollTop = oldScroll;
    highlightSelection();
  }
  function switchView(next) {
    view = next;
    updateViewSummary();
    if (view === "analysis") renderOverview();
  }

  function el(tag, text = "", className = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function time(value) {
    const parsed = new Date(value);
    return value && Number.isFinite(parsed.getTime()) ? date.format(parsed) : "시간 미확정";
  }
  function state(text, error = false) {
    byId("timeline-state").textContent = text;
    byId("timeline-state").classList.toggle("error", error);
  }
  async function get(path) {
    const response = await fetch(path, {cache: "no-store"});
    if (!response.ok) throw new Error("Timeline unavailable");
    return response.json();
  }
  function remember(message = "") {
    const hash = new URLSearchParams();
    if (selected) hash.set("session", selected);
    if (message) hash.set("message", message);
    history.replaceState(null, "", "#" + hash.toString());
    if(window.parent!==window)window.parent.postMessage({type:'context-graph-reader-position',session:selected},location.origin);
  }
  function filtered() {
    const term = byId("session-search").value.trim().toLocaleLowerCase();
    const project = byId("session-project").value;
    return sessions.filter(session => sessionKind(session)===sessionKindFilter &&
      (sessionKindFilter!=="subagent" || parentKey(session)===sessionParentFilter) && (!project || session.project === project) &&
      (!byId("analysed-only").checked || session.hasAnalysis) &&
      (!term || ([sessionTitle(session), session.title, session.project, session.id].join(" ")).toLocaleLowerCase().includes(term)));
  }
  function renderSessions() {
    scopeControls();
    const visible = filtered();
    byId("session-count").textContent = `${number.format(visible.length)} / ${number.format(sessions.length)}개 세션`;
    const list = byId("session-list");
    const fragment = document.createDocumentFragment();
    for (const session of visible) {
      const button = el("button", "", "session");
      button.type = "button";
      button.dataset.sessionId = session.id;
      button.setAttribute("aria-pressed", String(session.id === selected));
      button.title = `${sessionTitle(session)}\n세션 ID: ${session.id}`;
      const heading = el("span", "", "session-heading");
      heading.append(el("strong", sessionTitle(session)));
      if (session.displayTitle) heading.append(el("span", "표시 이름", "session-title-source"));
      else if (session.titleSource === "first_user") heading.append(el("span", "첫 요청", "session-title-source"));
      else if (session.titleSource === "own_request") heading.append(el("span", "분기 후 요청", "session-title-source"));
      button.append(heading, el("span", `${session.provider==='claude'?'Claude Code':'Codex'} · ${session.project}${session.branch ? " · " + session.branch : ""}`, "muted"),
        el("span", `메시지 ${number.format(session.messageCount)}개 · ${session.hasAnalysis ? "분석 " + number.format(session.analysisItemCount) + "개" : "분석 대기"}`, "muted"),
        el("time", time(session.lastMessageAt), "muted"));
      const helpers=sessions.filter(item=>sessionKind(item)==="subagent" && parentKey(item)===session.id).length;
      if(sessionKind(session)==="primary" && helpers) button.append(el("span",`보조 작업 ${helpers}개 · 세션 종류에서 확인`,"muted"));
      button.addEventListener("click", () => choose(session.id).catch(showError));
      fragment.append(button);
    }
    if (!visible.length) fragment.append(el("p", "조건에 맞는 세션이 없습니다.", "empty"));
    list.replaceChildren(fragment);
  }
  async function catalog() {
    const result = await get("timeline/sessions.json");
    sessions = result.sessions;
    const project = byId("session-project");
    const previous = project.value;
    project.replaceChildren(new Option("모든 프로젝트", ""));
    for (const value of [...new Set(sessions.map(session => session.project))].sort()) project.append(new Option(value, value));
    project.value = [...project.options].some(option => option.value === previous) ? previous : "";
    renderSessions();
  }
  function analysisButton(id, compact = false) {
    const node = data.analysis.nodes.find(node => node.id === id);
    if (!node) return null;
    const button = el("button", "", "analysis-link" + (compact ? " compact" : ""));
    button.type = "button";
    button.dataset.analysisId = id;
    button.dataset.kind = group(node);
    button.setAttribute("aria-pressed", String(id === selectedNode));
    button.append(el("span", labels.classification[node.classification] || "미분류", "analysis-type"), el("strong", itemTitle(node), "analysis-title"));
    if (!compact && node.summary) button.append(el("span", node.summary, "analysis-excerpt"));
    button.addEventListener("click", () => {
      showAnalysis(id);
      detail.parentElement.scrollTop = 0;
    });
    return button;
  }
  function renderMessages(scrollToEnd = false) {
    const opened = new Set([...messages.querySelectorAll("details[open]")].map(node => node.dataset.stateKey));
    const oldScroll = messages.scrollTop;
    const fragment = document.createDocumentFragment();
    for (const message of data.messages) {
      const article = el("article", "", "message " + (message.role === "user" ? "user" : "assistant"));
      article.dataset.messageId = message.id;
      if (message.id === locatedMessageId) article.classList.add("jumped");
      const heading = el("div", "", "message-heading");
      const role = message.role === "user" ? "사용자" : "어시스턴트";
      heading.append(el("span", message.role === "user" ? "나" : "AI", "avatar"), el("strong", role, "speaker"), el("span", `#${message.index + 1}`, "muted"), el("time", time(message.timestamp), "muted"));
      article.append(heading);
      article.append(el("p", message.content.slice(0, 240) + (message.content.length > 240 ? "…" : ""), "message-preview"));
      const raw = el("details", "", "raw-message");
      raw.dataset.stateKey = message.id + ":raw";
      raw.open = opened.has(raw.dataset.stateKey) || jumpMessage === message.id;
      const fullText = el("pre");
      appendHighlight(fullText, message.content, highlightTerm);
      raw.append(el("summary", `원문 자세히 보기 · ${number.format(message.content.length)}자`), fullText);
      const identifiers = el("details", "", "record-info");
      identifiers.dataset.stateKey = message.id + ":ids";
      identifiers.open = opened.has(identifiers.dataset.stateKey);
      identifiers.append(el("summary", "기록 정보"), el("small", `원문 ID: ${message.id} · 대화 ID: ${message.transcriptId}`, "identifier"));
      raw.append(identifiers);
      article.append(raw);
      const statusText = {linked: `이 메시지에서 정리한 내용 ${message.analysisNodeIds.length}개`, analysed: "",
        pending: "", unconfirmed: "분석 범위 미확정"}[message.analysisState];
      if (statusText) article.append(el("p", statusText, "message-state " + message.analysisState));
      const links = el("div", "", "analysis-links");
      for (const id of message.analysisNodeIds) {
        const button = analysisButton(id);
        if (button) links.append(button);
      }
      article.append(links);
      fragment.append(article);
    }
    if (!data.messages.length) fragment.append(el("p", "수집된 사용자·어시스턴트 메시지가 없습니다.", "empty"));
    messages.replaceChildren(fragment);
    highlightSelection();
    if (jumpMessage) {
      const target = [...messages.querySelectorAll("article")].find(node => node.dataset.messageId === jumpMessage);
      if (target) {
        target.classList.add("jumped");
        (target.querySelector("pre mark") || target).scrollIntoView({block: "center"});
        remember(jumpMessage);
        jumpMessage = "";
      }
    } else if (scrollToEnd) messages.scrollTop = messages.scrollHeight;
    else messages.scrollTop = oldScroll;
  }
  function showAnalysis(id) {
    selectedNode = id;
    unplacedMode = false;
    const node = data.analysis.nodes.find(node => node.id === id);
    if (!node) {
      closeDetail();
      return;
    }
    openDetail();
    const oldScroll = detail.parentElement.scrollTop;
    const fragment = document.createDocumentFragment();
    fragment.append(el("h3", itemTitle(node)), el("p", [labels.classification[node.classification] || node.classification,
      labels.status[node.status]].filter(Boolean).join(" · "), "badge"));
    for (const [label, value] of [["요약", node.summary], ["상세", node.detail], ["분류 이유", node.rationale]]) {
      if (value) fragment.append(el("h4", label), el("p", value));
    }
    const identity = el("details", "", "record-info");
    identity.append(el("summary", "분석 기록 정보"), el("small", `기존 분석 ID: ${node.id}`, "identifier"));
    fragment.append(identity);
    const anchors = data.analysis.anchors[id] || [];
    if (!anchors.length) fragment.append(el("p", "현재 메시지에 연결할 시점이 확정되지 않은 기존 분석입니다. 원래 근거와 관계는 유지됩니다.", "notice"));
    fragment.append(el("h4", `원문 근거 (${(node.evidence || []).length})`));
    for (const evidence of node.evidence || []) {
      const box = el("div", "", "evidence");
      box.append(el("p", `${evidence.messageId || "메시지 ID 없음"} · ${evidence.verified === true ? "저장된 검증 근거" : "검증되지 않은 근거"}`, "muted"),
        el("blockquote", evidence.quote || ""));
      const anchor = anchors.find(anchor => anchor.sourceMessageId === evidence.messageId);
      if (anchor) {
        const button = el("button", `메시지 #${anchor.index + 1}로 이동`);
        button.type = "button";
        button.addEventListener("click", () => jump(anchor).catch(showError));
        box.append(button);
      }
      fragment.append(box);
    }
    const relations = data.analysis.relations.filter(relation => relation.from === id || relation.to === id);
    fragment.append(el("h4", `기존 관계 (${relations.length})`));
    for (const relation of relations) {
      const line = el("div", "", "relation");
      line.append(el("p", `${relation.from === id ? "→" : "←"} ${labels.relation[relation.type] || relation.type}`));
      const other = relation.from === id ? relation.to : relation.from;
      const button = analysisButton(other, true);
      line.append(button || el("span", other));
      if (relation.note) line.append(el("p", relation.note, "muted"));
      fragment.append(line);
    }
    detail.replaceChildren(fragment);
    detail.parentElement.scrollTop = oldScroll;
    highlightSelection();
  }
  function showUnplaced() {
    selectedNode = "";
    unplacedMode = true;
    openDetail();
    const fragment = document.createDocumentFragment();
    fragment.append(el("h3", "시점 미확정 분석"), el("p", "근거 메시지와 확실히 연결되지 않은 항목입니다. 연결을 추정하지 않고 기존 분석을 별도로 표시합니다.", "muted"));
    for (const id of data.analysis.unplacedIds) {
      const button = analysisButton(id);
      if (button) fragment.append(button);
    }
    detail.replaceChildren(fragment);
    highlightSelection();
  }
  async function load(offset = null, scrollToEnd = false) {
    if (!selected) return;
    const session = selected;
    const serial = ++requestSerial;
    const query = new URLSearchParams({session, limit: "100"});
    if(offset===null && restoreReading && !restoreReading.follow)offset=restoreReading.offset;
    if (offset !== null) query.set("offset", String(offset));
    else if (jumpMessage) query.set("message", jumpMessage);
    const result = await get("timeline/messages.json?" + query);
    if (serial !== requestSerial || selected !== session) return;
    // Search can see a newly collected session before the status-driven catalog
    // refresh. Its authoritative header must reveal the selected scope now, or
    // the next poll can replace this conversation with an unrelated one.
    const index=sessions.findIndex(item=>item.id===session);
    if(result.session?.id===session && JSON.stringify(sessions[index])!==JSON.stringify(result.session)) {
      if(index===-1) sessions.push(result.session);
      else sessions[index]=result.session;
      revealSession(session);
      renderSessions();
    }
    const changed = !data || data.revision !== result.revision || data.offset !== result.offset;
    data = result;
    byId("conversation-title").textContent = sessionTitle(data.session);
    byId("conversation-meta").textContent = `${data.session.project} · ${number.format(data.total)}개 메시지 · ${data.session.hasAnalysis ? "저장된 분석 있음" : "분석 대기"}`;
    updateViewSummary();
    byId("older").disabled = data.offset === 0;
    byId("newer").disabled = data.offset + data.messages.length >= data.total;
    byId("unplaced").hidden = !data.analysis.unplacedIds.length;
    byId("unplaced").textContent = `시점 미확정 분석 ${data.analysis.unplacedIds.length}개`;
    if (changed || jumpMessage) renderMessages(scrollToEnd);
    if(restoreReading){
      const saved=restoreReading;restoreReading=null;
      for(const raw of messages.querySelectorAll('.raw-message'))if(saved.opened?.includes(raw.dataset.stateKey.split(':')[0]))raw.open=true;
      const target=[...messages.querySelectorAll('.message')].find(e=>e.dataset.messageId===saved.message);
      if(target && !saved.follow)messages.scrollTop+=target.getBoundingClientRect().top-messages.getBoundingClientRect().top+Math.min(Math.max(0,Number(saved.within)||0),Math.max(0,target.offsetHeight-20));
    }
    remember();persistReading();
    if (changed && view === "analysis") renderOverview();
    if (selectedNode) showAnalysis(selectedNode);
    else if (unplacedMode) showUnplaced();
    state("자동 갱신 중");
  }
  const narrow = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 900px)") : {matches: false};
  function setSessionsHidden(hidden) {
    document.body.classList.toggle("sessions-hidden", hidden);
    byId("toggle-sessions").textContent = hidden ? "세션 목록 보이기" : "세션 목록 숨기기";
    byId("toggle-sessions").setAttribute("aria-expanded", String(!hidden));
  }
  async function choose(sid, targetMessage = "") {
    if (narrow.matches && sid) { setSessionsHidden(true); byId("conversation-title").scrollIntoView?.({block: "start"}); }
    persistReading();
    ++requestSerial;
    selected = sid;
    revealSession(sid);
    data = null;
    jumpMessage = targetMessage;
    locatedMessageId = targetMessage;
    restoreReading=!targetMessage?savedReading(sid):null;
    selectedNode = "";
    unplacedMode = false;
    closeDetail();
    category = "all";
    overview.replaceChildren(el("p", "대화를 선택해 정리된 내용을 확인하세요.", "empty"));
    byId("analysis-filters").replaceChildren();
    updateViewSummary();
    follow.checked = restoreReading?Boolean(restoreReading.follow):!targetMessage;
    if (targetMessage) switchView("conversation");
    remember(targetMessage);
    renderSessions();
    detail.replaceChildren(el("p", "메시지 아래의 분석 항목을 선택하세요.", "muted"));
    if (!sid) {
      messages.replaceChildren(el("p", "세션을 선택하세요.", "empty"));
      byId("conversation-title").textContent = "세션을 선택하세요";
      byId("conversation-meta").textContent = "";
      byId("timeline-summary").textContent = "";
      byId("unplaced").hidden = true;
      for (const id of ["older", "newer", "latest", "unplaced"]) byId(id).disabled = true;
      return;
    }
    for (const id of ["latest", "unplaced"]) byId(id).disabled = false;
    await load(null, !targetMessage);
  }
  async function jump(anchor) {
    switchView("conversation");
    follow.checked = false;
    jumpMessage = anchor.messageId;
    remember(jumpMessage);
    await load(Math.floor(anchor.index / 100) * 100);
    if (innerWidth < 1250) closeDetail();
  }
  function showError() { state("연결 확인 필요 · 마지막 표시 유지", true); }
  async function applyFilter() {
    renderSessions();
    const visible = filtered();
    if (!visible.some(session => session.id === selected)) await choose(visible[0]?.id || "");
  }
  for (const id of ["session-search", "session-project", "analysed-only"]) {
    byId(id).addEventListener(id === "session-search" ? "input" : "change", () => applyFilter().catch(showError));
  }
  for(const id of ["session-kind","session-parent"]) byId(id).addEventListener("change",()=>{
    if(id==="session-kind") sessionKindFilter=byId(id).value;
    else sessionParentFilter=byId(id).value;
    scopeControls();rememberScope();applyFilter().catch(showError);
  });
  byId("older").addEventListener("click", () => { follow.checked = false; jumpMessage = ""; remember(); if (data) load(Math.max(0, data.offset - 100)).catch(showError); });
  byId("newer").addEventListener("click", () => { follow.checked = false; jumpMessage = ""; remember(); if (data) load(data.offset + 100).catch(showError); });
  byId("latest").addEventListener("click", () => { follow.checked = true; jumpMessage = ""; remember(); load(null, true).catch(showError); });
  follow.addEventListener("change", () => { if (follow.checked) { jumpMessage = ""; remember(); load(null, true).catch(showError); } });
  byId("unplaced").addEventListener("click", showUnplaced);
  byId("fulltext-form").addEventListener("submit", event => {
    event.preventDefault();
    const query = byId("fulltext-query").value.trim();
    if (!query) return;
    searchArguments = {query, project:byId("session-project").value, role:byId("fulltext-role").value,
      session:byId("fulltext-scope").value === "session" ? selected : ""};
    if (byId("fulltext-scope").value === "session" && !selected) return;
    if (!searchDialog.open) searchDialog.showModal();
    runSearch();
  });
  byId("close-search").addEventListener("click", () => searchDialog.close());
  byId("reopen-search").addEventListener("click", () => { if (!searchDialog.open) searchDialog.showModal(); });
  byId("search-previous").addEventListener("click", () => { if (searchPage) runSearch(Math.max(0,searchPage.offset-30)); });
  byId("search-next").addEventListener("click", () => { if (searchPage) runSearch(searchPage.offset+30); });
  byId("view-conversation").addEventListener("click", () => switchView("conversation"));
  byId("view-analysis").addEventListener("click", () => switchView("analysis"));
  byId("close-analysis").addEventListener("click", closeDetail);
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeDetail(); });
  byId("toggle-sessions").addEventListener("click", () => {
    const hidden = !document.body.classList.contains("sessions-hidden");
    setSessionsHidden(hidden);
  });
  function readingSize(value) {
    const size = ["normal", "large", "largest"].includes(value) ? value : "normal";
    document.body.dataset.reading = size;
    byId("reading-size").value = size;
  }
  try { readingSize(localStorage.getItem("context-graph-reading-size")); } catch (_) { readingSize("normal"); }
  byId("reading-size").addEventListener("change", () => {
    readingSize(byId("reading-size").value);
    try { localStorage.setItem("context-graph-reading-size", byId("reading-size").value); } catch (_) {}
  });
  let readingTimer=0;
  messages.addEventListener('scroll',()=>{clearTimeout(readingTimer);readingTimer=setTimeout(persistReading,120);});
  messages.addEventListener('toggle',persistReading,true);
  window.addEventListener('pagehide',persistReading);

  async function poll(initial = false) {
    try {
      const status = await get("status.json");
      const key = JSON.stringify([status.messageCount, status.sessionCount, status.analysisRevision || status.graphRevision]);
      if (initial || key !== statusKey) {
        await catalog();
        if(initial && selected) {revealSession(selected);renderSessions();}
        const visible = filtered();
        if (!visible.some(session => session.id === selected)) await choose(visible[0]?.id || "");
        else {
          const atEnd = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 100;
          await load(follow.checked && atEnd ? null : data?.offset ?? null, follow.checked && atEnd);
        }
        statusKey = key;
      }
      if (status.state === "error" || status.state === "warning") state("수집 상태 확인 필요 · 마지막 기록 표시", true);
      else if(status.collector?.state==='stopped')state('수집기 멈춤 · 마지막 기록 표시',true);
      else state("자동 갱신 중");
    } catch (_) { showError(); }
    finally { setTimeout(poll, 2000); }
  }
  if (location.protocol === "http:" || location.protocol === "https:") poll(true);
  else state("수집기 주소에서 타임라인을 열어주세요", true);
})();
