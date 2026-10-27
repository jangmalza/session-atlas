(() => {
  "use strict";
  const frame = document.getElementById("live-frame");
  const viewLabels={graph:"세션 탐색",map:"주제 연결 지도",timeline:"원문 읽기·검색"};
  for(const button of document.querySelectorAll("nav button[data-view]")) button.textContent=viewLabels[button.dataset.view];
  const mapNotice=document.createElement("section");mapNotice.id="map-context";mapNotice.hidden=true;
  const noticeTitle=document.createElement("strong");noticeTitle.textContent="주제 연결 지도 · 저장된 분석";
  const noticeText=document.createElement("p");noticeText.textContent="세션은 하나의 채팅 기록이고, 주제는 그 안에서 다룬 내용입니다. 이 지도는 내보낸 분석 결과입니다. 세션을 선택해 주제별 질문·답변을 읽으려면 아래 버튼을 누르세요.";
  const noticeButton=document.createElement("button");noticeButton.type="button";noticeButton.dataset.view="graph";noticeButton.textContent="세션 선택하고 질문·답변 보기";
  mapNotice.append(noticeTitle,noticeText,noticeButton);frame.parentElement.prepend(mapNotice);
  const buttons = Array.from(document.querySelectorAll("button[data-view]"));
  const stateLabel = document.getElementById("collector-state");
  let status = JSON.parse(document.getElementById("live-status").textContent);
  const requestedView = new URLSearchParams(location.search).get("view");
  let active = ["map", "timeline"].includes(requestedView) ? requestedView : "graph";
  let loadedRevision = "";
  let hashes = {graph: "", map: "", timeline: ""};
  try { hashes = {...hashes, ...JSON.parse(sessionStorage.getItem("context-graph-live-positions") || "{}")}; } catch (_) {}
  const number = new Intl.NumberFormat("ko-KR");
  const time = new Intl.DateTimeFormat("ko-KR", {dateStyle: "short", timeStyle: "medium"});

  function validPosition(view, hash) {
    return view === "timeline" ? /^#session=[A-Za-z0-9-]{1,128}(?:&message=[a-f0-9]{64})?$/.test(hash) : /^#node=[a-z0-9-]{1,128}$/.test(hash);
  }
  function savePosition() {
    try {
      const hash = frame.contentWindow.location.hash;
      hashes[active] = validPosition(active, hash) ? hash : "";
      sessionStorage.setItem("context-graph-live-positions", JSON.stringify(hashes));
    } catch (_) {}
  }
  function show() {
    if (active === "map" && !status.mapReady && status.graphReady) active = "graph";
    for (const button of buttons) button.setAttribute("aria-pressed", String(button.dataset.view === active));
    const ready = status[active + "Ready"];
    if (!ready || loadedRevision) return;
    const revision = status[active + "Revision"];
    let hash = validPosition(active, hashes[active]) ? hashes[active] : "";
    if (active === "timeline") {
      const parameters = new URLSearchParams(location.search);
      if (parameters.has("session")) {
        const position=new URLSearchParams({session:parameters.get("session")});
        if(parameters.has("message")) position.set("message",parameters.get("message"));
        const target = "#" + position;
        if (validPosition(active, target)) hash = target;
      }
    }
    const mode=active==="graph" && new URLSearchParams(location.search).get("mode")==="mind" ? "&mode=mind" : "";
    const target = active + ".html?rev=" + encodeURIComponent(revision) + mode + hash;
    // Some embedded Chromium views retain the initial about:blank document
    // while the parent is navigating. Start after parent load so the initial
    // iframe navigation is not cancelled by the parent document committing.
    const recoverEmptyFrame=() => {
      try {
        const child=frame.contentDocument;
        if(frame.contentWindow.location.href === "about:blank" || (child?.readyState==="complete" && !child.body?.firstElementChild))
          frame.contentWindow.location.replace(new URL(target,location.href).href);
      } catch (_) {}
    };
    const navigateFrame=()=>{
      frame.hidden=false;
      frame.src=target;
      setTimeout(recoverEmptyFrame,300);
      setTimeout(recoverEmptyFrame,1500);
    };
    if(document.readyState==="complete") navigateFrame();
    else window.addEventListener("load",navigateFrame,{once:true});
    frame.title = viewLabels[active];
    frame.hidden = false;
    document.getElementById("initial-wait").hidden = true;
    loadedRevision = revision;
  }
  function paint(next, refreshAnalysis = false) {
    const nextRevision = next[active + "Revision"];
    if (refreshAnalysis && loadedRevision && nextRevision && loadedRevision !== nextRevision) {
      if(active==='graph' && next.analysisRevision===status.analysisRevision && next.uiRevision===status.uiRevision){
        loadedRevision=nextRevision;
        frame.contentWindow.postMessage({type:'context-graph-refresh-display'},location.origin);
      }else{
      if(document.querySelector('#analysis-run[open],#analysis-settings[open]'))return;
      // Only explicit analysis/snapshot updates reload the view. Raw message
      // collection leaves the current map, selection and open dialogs intact.
      savePosition();
      location.reload();
      return;
      }
    }
    status = next;
    mapNotice.hidden=active!=="map";document.body.classList.toggle("map-mode",active==="map");
    const fields = {"session-count":"sessionCount", "message-count":"messageCount", "analysed-count":"analysedSessionCount",
      "pending-count":"pendingSessionCount", "pending-message-count":"pendingMessageCount"};
    for (const [name, field] of Object.entries(fields)) {
      document.querySelector('[data-testid="'+name+'"]').textContent = number.format(status[field] || 0);
    }
    stateLabel.dataset.state = status.state;
    stateLabel.textContent = status.state === "watching" ? "자동 수집 중" : status.state === "warning" ? "일부 파일을 읽지 못함" :
      status.state === "error" ? "갱신 실패 · 마지막 결과 표시" : "수집 준비 중";
    if (status.readOnlyViewer && status.state === "watching") stateLabel.textContent = "저장된 기록 보기";
    if(status.collector?.state==='running')stateLabel.textContent='자동 수집 중';
    else if(status.collector?.state==='stopped'){stateLabel.textContent='수집기 멈춤 · 마지막 기록 표시';stateLabel.dataset.state='warning';}
    else if(status.collector?.state==='warning'){stateLabel.textContent='일부 기록을 읽지 못함';stateLabel.dataset.state='warning';}
    else if(status.readOnlyViewer && status.state==='watching')stateLabel.textContent='저장된 기록 보기';
    const date = new Date(status.lastCheckedUtc);
    document.getElementById("last-checked").textContent = Number.isFinite(date.getTime()) ? "마지막 확인: " + time.format(date) : "첫 수집을 기다리고 있습니다.";
    show();
  }
  for (const button of buttons) button.addEventListener("click", () => {
    if (button.dataset.view === active) return;
    savePosition();
    if(button.dataset.view==="graph") {
      hashes.graph="";
      try {sessionStorage.setItem("context-graph-live-positions",JSON.stringify(hashes));} catch (_) {}
    }
    const url = new URL(location.href);
    url.searchParams.delete("session");
    url.searchParams.delete("message");
    url.searchParams.set("view", button.dataset.view);
    location.assign(url.href);
  });
  window.addEventListener("message", event => {
    if(event.origin !== location.origin || event.source !== frame.contentWindow) return;
    if(event.data?.type==="context-graph-open-mindmap"){
      // The timeline has no tab bar to return through; its own button brings the mind map back.
      savePosition();
      const url=new URL(location.href);
      url.searchParams.delete("session");url.searchParams.delete("message");url.searchParams.set("view","graph");
      location.assign(url.href);return;
    }
    if(!["context-graph-open-source","context-graph-open-session","context-graph-reader-position"].includes(event.data?.type)) return;
    const session=event.data.session, message=event.data.message;
    if(typeof session!=="string" || !/^[A-Za-z0-9-]{1,128}$/.test(session)) return;
    if(event.data.type==='context-graph-reader-position'){
      if(active!=='timeline')return;
      hashes.timeline='#'+new URLSearchParams({session});
      try{sessionStorage.setItem('context-graph-live-positions',JSON.stringify(hashes));}catch(_){}
      const position=new URL(location.href);position.searchParams.set('session',session);position.searchParams.delete('message');history.replaceState(null,'',position.href);return;
    }
    if(event.data.type==="context-graph-open-source" && (typeof message!=="string" || !/^[a-f0-9]{64}$/.test(message))) return;
    savePosition();
    const url=new URL(location.href);
    const position=new URLSearchParams({view:"timeline",session});
    if(event.data.type==="context-graph-open-source") position.set("message",message);
    url.search=position.toString();
    location.assign(url.href);
  });
  paint(status);
  if (location.protocol !== "http:" && location.protocol !== "https:") {
    stateLabel.textContent = "저장된 화면 · 갱신 없음";
    return;
  }
  async function poll() {
    try {
      const response = await fetch("status.json", {cache: "no-store"});
      if (!response.ok) throw new Error("Unavailable");
      paint(await response.json(), true);
    } catch (_) {
      stateLabel.dataset.state = "error";
      stateLabel.textContent = "연결 끊김 · 마지막 결과 표시";
    } finally {
      setTimeout(poll, 1500);
    }
  }
  setTimeout(poll, 1500);
})();
