/* Local reading tools. Persist identifiers only; never persist source text. */
const SessionAtlasTools = (() => {
  "use strict";
  const validId=value=>typeof value==="string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
  function normalize(value) {
    const ids=values=>[...new Set((Array.isArray(values)?values:[]).filter(validId))];
    return {version:1,favorites:ids(value?.favorites).slice(0,200),recent:ids(value?.recent).slice(0,20),
      positions:(Array.isArray(value?.positions)?value.positions:[]).filter(p=>validId(p?.session)&&validId(p?.message))
        .map(p=>({session:p.session,message:p.message})).filter((p,i,a)=>a.findIndex(x=>x.session===p.session)===i).slice(0,100)};
  }
  function navigationStore(storage) {
    const key="session-atlas-navigation-v1";
    let data=normalize(null);
    try {const raw=storage?.getItem(key);if(raw && raw.length<64000)data=normalize(JSON.parse(raw));} catch (_) {}
    function save(){try{storage?.setItem(key,JSON.stringify(data));}catch(_){}return snapshot();}
    function snapshot(){return normalize(data);}
    return {snapshot,
      visit(session){if(validId(session)){data.recent=[session,...data.recent.filter(x=>x!==session)].slice(0,20);save();}},
      toggle(session){if(!validId(session))return;data.favorites=data.favorites.includes(session)?data.favorites.filter(x=>x!==session):[session,...data.favorites].slice(0,200);save();},
      remember(session,message){if(validId(session)&&validId(message)){data.positions=[{session,message},...data.positions.filter(x=>x.session!==session)].slice(0,100);save();}},
      position(session){return data.positions.find(p=>p.session===session)?.message || "";}};
  }
  function matchingEntries(groups,hit) {
    const group=groups.find(g=>g.sessionId===hit.sessionId);if(!group)return [];
    const ids=new Set(Array.isArray(hit.analysisNodeIds)?hit.analysisNodeIds:[]);
    return group.entries.filter(entry=>entry.anchor?.messageId===hit.id || ids.has(entry.source.source_node) ||
      entry.answers.some(answer=>answer.anchor?.messageId===hit.id || ids.has(answer.source?.source_node)) ||
      entry.answerEvidence.some(evidence=>evidence.messageId===hit.id));
  }
  function create(options) {
    const {document,fetch,storage,live,getSessions,getTargets,onNavigate,onLayout,onLibraryChange,onFullPage}=options;
    const byId=id=>document.getElementById(id),nav=navigationStore(storage);
    const el=(tag,text="",className="")=>{const e=document.createElement(tag);e.textContent=text;e.className=className;return e;};
    const button=(text,fn)=>{const e=el("button",text);e.type="button";e.addEventListener("click",fn);return e;};
    const title=sid=>{const s=getSessions().find(s=>s.id===sid);return s?.displayTitle||s?.title||"세션을 찾을 수 없습니다";};
    let selectedSession="",reader=null,serial=0,lastRequest=null,searchSerial=0,searchPage=null,searchQuery="",scrollTimer=0;
    const panel=byId("source-reader"),body=byId("source-reader-body");
    function updateButtons(){
      const exists=!!getSessions().find(s=>s.id===selectedSession),saved=nav.snapshot();
      byId("explorer-star").disabled=!exists;
      byId("explorer-star").textContent=saved.favorites.includes(selectedSession)?"★ 즐겨찾기 해제":"☆ 즐겨찾기";
      byId("explorer-star").setAttribute("aria-pressed",String(saved.favorites.includes(selectedSession)));
      byId("explorer-resume").disabled=!live||!exists||!nav.position(selectedSession);
    }
    function updateSession(session){
      if(session && session!==selectedSession)nav.visit(session);
      selectedSession=session||"";updateButtons();
      if((reader?.session.id || lastRequest?.session) && (reader?.session.id || lastRequest?.session)!==selectedSession)closeReader();
    }
    function closeReader(){serial++;reader=null;lastRequest=null;clearTimeout(scrollTimer);panel.hidden=true;document.body.classList.remove("has-source-reader");onLayout?.();}
    function unavailable(message){
      serial++;reader=null;lastRequest=null;panel.hidden=false;document.body.classList.add("has-source-reader");
      body.replaceChildren(el("p",message,"reader-empty"));byId("source-reader-title").textContent="원문 연결 확인";
      byId("source-reader-status").textContent="";
      for(const id of ["source-reader-older","source-reader-newer","source-reader-full","source-reader-retry"])byId(id).disabled=true;
      onLayout?.();
    }
    async function openReader(session,message="",offset=null) {
      if(!live){unavailable("원문은 실행 중인 뷰어에서 읽을 수 있습니다.");return;}
      if(!validId(session)||message && !validId(message))return;
      const request=++serial;lastRequest={session,message,offset};reader=null;
      panel.hidden=false;document.body.classList.add("has-source-reader");
      byId("source-reader-title").textContent=title(session);
      byId("source-reader-status").textContent="원문을 불러오는 중…";
      body.replaceChildren();
      for(const id of ["source-reader-older","source-reader-newer","source-reader-full","source-reader-retry"])byId(id).disabled=true;
      onLayout?.();
      const query=new URLSearchParams({session,limit:"7"});
      if(offset!==null)query.set("offset",String(offset));else if(message)query.set("message",message);
      try {
        const response=await fetch("timeline/messages.json?"+query,{cache:"no-store"});if(!response.ok)throw new Error("Unavailable");
        const value=await response.json();if(request!==serial)return;
        if(value.session?.id!==session || !Array.isArray(value.messages) ||
          message && offset===null && !value.messages.some(m=>m.id===message))throw new Error("Unverified message");
        reader=value;
        const target=value.messages.find(m=>m.id===message) || value.messages[0];
        lastRequest={session,message:target?.id||"",offset:null};
        byId("source-reader-title").textContent=title(session);
        byId("source-reader-status").textContent=value.messages.length?`원문 #${value.offset+1}–${value.offset+value.messages.length} / ${value.total}`:"저장된 메시지가 없습니다.";
        const fragment=document.createDocumentFragment();let targetElement=null;
        for(const row of value.messages){
          const article=el("article","","reader-message"+(row.id===target?.id?" reader-target":""));article.dataset.messageId=row.id;
          const meta=el("div","","reader-message-meta");
          meta.append(el("strong",row.role==="user"?"사용자":"어시스턴트"),el("span",`원문 #${row.index+1}`));
          const stamp=Date.parse(row.timestamp||"");if(Number.isFinite(stamp))meta.append(el("time",new Intl.DateTimeFormat("ko-KR",{dateStyle:"short",timeStyle:"short"}).format(new Date(stamp))));
          article.append(meta,el("pre",row.content,"reader-original"));
          article.addEventListener("click",()=>{nav.remember(session,row.id);updateButtons();});
          fragment.append(article);if(row.id===target?.id)targetElement=article;
        }
        body.replaceChildren(fragment);
        byId("source-reader-older").disabled=value.offset===0;
        byId("source-reader-newer").disabled=value.offset+value.messages.length>=value.total;
        byId("source-reader-full").disabled=false;
        if(target){nav.remember(session,target.id);updateButtons();}
        targetElement?.scrollIntoView({block:"center",behavior:"auto"});onLayout?.();
      } catch (_) {
        if(request!==serial)return;
        byId("source-reader-status").textContent="원문을 불러오지 못했습니다. 다시 시도해 주세요.";
        byId("source-reader-retry").disabled=false;
      }
    }
    body.addEventListener("scroll",()=>{
      clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{
        if(!reader||panel.hidden)return;const rect=body.getBoundingClientRect();
        const first=[...body.querySelectorAll(".reader-message")].find(e=>e.getBoundingClientRect().bottom>rect.top+3);
        if(first){nav.remember(reader.session.id,first.dataset.messageId);lastRequest={session:reader.session.id,message:first.dataset.messageId,offset:null};updateButtons();}
      },160);
    });
    byId("source-reader-close").addEventListener("click",closeReader);
    byId("source-reader-retry").addEventListener("click",()=>{if(lastRequest)openReader(lastRequest.session,lastRequest.message,lastRequest.offset);});
    byId("source-reader-older").addEventListener("click",()=>{if(reader)openReader(reader.session.id,"",Math.max(0,reader.offset-7));});
    byId("source-reader-newer").addEventListener("click",()=>{if(reader)openReader(reader.session.id,"",reader.offset+reader.messages.length);});
    byId("source-reader-full").addEventListener("click",()=>{if(lastRequest)onFullPage(lastRequest.session,lastRequest.message);});
    byId("explorer-star").addEventListener("click",()=>{if(selectedSession){nav.toggle(selectedSession);updateButtons();onLibraryChange?.();}});
    byId("explorer-resume").addEventListener("click",()=>{const message=nav.position(selectedSession);if(message)openReader(selectedSession,message);});
    function library(){
      const host=byId("explorer-library-items");host.replaceChildren();const saved=nav.snapshot();
      for(const [label,ids] of [["즐겨찾기",saved.favorites],["최근 본 세션",saved.recent]]){
        host.append(el("h3",label));if(!ids.length)host.append(el("p","아직 저장된 세션이 없습니다.","hint"));
        for(const sid of ids){const row=el("div","","library-row"),open=button(title(sid),()=>{byId("explorer-library").close();onNavigate({sessionId:sid});});
          open.disabled=!getSessions().some(s=>s.id===sid);row.append(open);
          if(label==="즐겨찾기")row.append(button("해제",()=>{nav.toggle(sid);library();updateButtons();}));
          if(nav.position(sid))row.append(button("이어서 읽기",()=>{byId("explorer-library").close();onNavigate({sessionId:sid});openReader(sid,nav.position(sid));}));
          host.append(row);
        }
      }
    }
    byId("explorer-saved").addEventListener("click",()=>{library();byId("explorer-library").showModal();});
    byId("explorer-library-close").addEventListener("click",()=>byId("explorer-library").close());
    const searchDialog=byId("explorer-search-dialog");
    function renderSearch(){
      if(!searchPage)return;const host=byId("explorer-search-results");host.replaceChildren();
      for(const hit of searchPage.results){
        const row=el("article","","explorer-result");
        row.append(el("strong",title(hit.sessionId)),el("p",`원문 #${hit.index+1} · ${hit.role==="user"?"사용자":"어시스턴트"}`,"hint"),el("p",hit.snippet,"search-source-snippet"));
        row.append(button("원문 읽기",()=>{searchDialog.close();onNavigate({sessionId:hit.sessionId,sourceMessage:hit.id});openReader(hit.sessionId,hit.id);}));
        const targets=getTargets(hit);
        for(const target of targets)row.append(button("마인드맵 · "+target.label,()=>{searchDialog.close();onNavigate(target);openReader(hit.sessionId,hit.id);}));
        if(!targets.length)row.append(el("p","확인된 질문 연결이 없습니다. 원문은 읽을 수 있습니다.","hint"));
        host.append(row);
      }
      if(!searchPage.results.length)host.append(el("p","일치하는 원문이 없습니다.","hint"));
      byId("explorer-search-status").textContent=`검색 결과 ${searchPage.total}개 · ${searchPage.total?searchPage.offset+1:0}–${searchPage.offset+searchPage.results.length}`;
      byId("explorer-search-previous").disabled=searchPage.offset===0;
      byId("explorer-search-next").disabled=searchPage.offset+searchPage.results.length>=searchPage.total;
    }
    async function search(offset=0){
      if(!live||!searchQuery)return;const request=++searchSerial;searchPage=null;
      byId("explorer-search-results").replaceChildren();byId("explorer-search-status").textContent="원문을 검색하는 중…";
      for(const id of ["explorer-search-previous","explorer-search-next"])byId(id).disabled=true;
      try{const response=await fetch("timeline/search.json",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:searchQuery,offset,limit:20})});
        if(!response.ok)throw new Error("Unavailable");const value=await response.json();if(request!==searchSerial)return;
        if(!Array.isArray(value.results))throw new Error("Invalid search");searchPage=value;renderSearch();
      }catch(_){if(request===searchSerial)byId("explorer-search-status").textContent="검색하지 못했습니다. 검색 버튼으로 다시 시도해 주세요.";}
    }
    byId("explorer-search").disabled=!live;
    byId("explorer-search").addEventListener("click",()=>{searchDialog.showModal();byId("explorer-search-query").focus();});
    byId("explorer-search-form").addEventListener("submit",event=>{event.preventDefault();searchQuery=byId("explorer-search-query").value.trim();if(searchQuery.length<=200)search(0);});
    byId("explorer-search-close").addEventListener("click",()=>{searchSerial++;searchDialog.close();});
    byId("explorer-search-previous").addEventListener("click",()=>{if(searchPage)search(Math.max(0,searchPage.offset-20));});
    byId("explorer-search-next").addEventListener("click",()=>{if(searchPage)search(searchPage.offset+searchPage.results.length);});
    updateButtons();
    return {openReader,closeReader,unavailable,updateSession,refresh:()=>{updateButtons();if(searchDialog.open)renderSearch();},
      snapshot:nav.snapshot,readerSession:()=>reader?.session.id||lastRequest?.session||""};
  }
  return {normalize,navigationStore,matchingEntries,create};
})();
if(typeof module!=="undefined" && module.exports)module.exports=SessionAtlasTools;
