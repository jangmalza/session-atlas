/* Derived display only. Pair stored answers; order by rechecked user anchors. */
const QuestionAnswers = (() => {
  "use strict";
  const isQuestion = node => ["open_question","answered_question"].includes(node.classification);
  const sessionTitle=session=>session?.displayTitle || session?.title || session?.project || "세션";
  function applySessionDisplay(session,result) {
    const name=result?.displayName;
    if(result?.saved!==true || result.session!==session.id ||
      !/^[a-f0-9]{64}$/.test(result.sessionDisplay?.revision || "") ||
      (name!==null && (typeof name!=="string" || !name || name!==name.trim() || [...name].length>120 ||
        /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(name))))throw new Error("Invalid session display response");
    const {displayTitle,...original}=session;
    return {...original,sessionDisplay:result.sessionDisplay,...(name===null?{}:{displayTitle:name})};
  }
  function topicStructure(structure) {
    const counts=new Map();
    for(const node of structure) counts.set(node.id,(counts.get(node.id)||0)+1);
    const nodes=new Map(structure.filter(node=>counts.get(node.id)===1).map(node=>[node.id,node]));
    const parents=new Map();
    for(const node of nodes.values()) for(const child of node.childIds || []) {
      if(!parents.has(child)) parents.set(child,new Set());
      parents.get(child).add(node.id);
    }
    function path(id) {
      let current=nodes.get(id); const visited=new Set(), topics=[];
      while(current) {
        if(visited.has(current.id)) return [];
        visited.add(current.id);
        if(current.kind==="topic") topics.unshift(current.id);
        const candidates=parents.get(current.id) || new Set();
        const declared=typeof current.parentId==="string" && current.parentId ? current.parentId : null;
        if(candidates.size>1 || (declared && candidates.size && !candidates.has(declared))) return [];
        const parent=declared || [...candidates][0];
        if(!parent) return topics;
        if(!nodes.has(parent)) return [];
        current=nodes.get(parent);
      }
      return topics;
    }
    const topics=[...nodes.values()].filter(node=>node.kind==="topic").map(node=>({id:node.id,
      title:node.displayTitle || node.title || "제목 없는 주제",originalTitle:node.title || "제목 없는 주제",path:path(node.id),
      ...(typeof node.displayTitle==="string" && node.displayTitle?{displayTitle:node.displayTitle}:{}),
      ...(node.displayGroup==="auxiliary"?{displayGroup:"auxiliary"}:{})}));
    for(const topic of topics) {
      const nearest=topic.path.slice().reverse().map(id=>nodes.get(id)?.displayGroup).find(group=>["primary","auxiliary"].includes(group));
      if(nearest==="auxiliary")topic.displayGroup="auxiliary";
      else if(nearest==="primary")delete topic.displayGroup;
    }
    return {topics,path,itemIds:[...nodes.values()].filter(node=>node.kind==="item").map(node=>node.id)};
  }
  function topicUserEvidence(context,structure) {
    const messages=new Map((context?.messages || []).map(message=>[message.id,message])), result=Object.create(null);
    for(const sourceNodeId of structure.itemIds) {
      const path=structure.path(sourceNodeId);
      for(const anchor of context?.analysis?.anchors?.[sourceNodeId] || []) {
        const message=messages.get(anchor.messageId);
        // Content eligibility is supplied by the read-only source reader. Old
        // exports without this flag cannot promote context-only user records.
        if(!message || message.role!=="user" || message.hasUserContent!==true ||
          !Number.isInteger(anchor.index) || anchor.index<0 || message.index!==anchor.index ||
          typeof anchor.sourceMessageId!=="string" || !anchor.sourceMessageId) continue;
        for(const topicId of path) if(!result[topicId] || anchor.index<result[topicId].index)
          result[topicId]={...anchor,sourceNodeId};
      }
    }
    return result;
  }
  function build(graph, contexts = {}) {
    const nodes = new Map(graph.nodes.map(node => [node.id,node]));
    const groups = new Map(), structures=new Map();
    for (const question of graph.nodes.filter(isQuestion)) {
      const sources = question.provenance.length ? question.provenance : [{session_id:"",source_node:""}];
      for (const source of sources) {
        const sid = source.session_id, context = contexts[sid];
        if (!groups.has(sid)) {
          const structure=topicStructure(context?.analysis.structure || []);structures.set(sid,structure);
          groups.set(sid,{sessionId:sid,title:context?.session?sessionTitle(context.session):question.project,project:context?.session.project || question.project,
            topics:structure.topics,itemPaths:Object.fromEntries(structure.itemIds.map(id=>[id,structure.path(id)])),
            topicDisplay:context?.session.topicDisplay || {},
            itemLabels:context?.analysis.itemLabels || {},evidenceTitles:context?.analysis.evidenceTitles || {},
            topicUserEvidence:topicUserEvidence(context,structure),entries:[]});
        }
        const group = groups.get(sid);
        // Duplicate provenance must not duplicate a question occurrence.
        if (group.entries.some(entry => entry.nodeId === question.id && entry.source.source_node === source.source_node)) continue;
        const messages = new Map((context?.messages || []).map(message => [message.id,message]));
        const anchorsFor = (provenance, role) => (context?.analysis.anchors[provenance.source_node] || [])
          .filter(anchor => {
            const message = messages.get(anchor.messageId);
            return message && message.index === anchor.index && message.role === role;
          }).slice().sort((a,b) => a.index-b.index);
        const anchor = anchorsFor(source,"user")[0] || null;
        const answers = [];
        for (const edge of graph.edges) {
          if (edge.type !== "answers" || edge.to !== question.id || !nodes.has(edge.from)) continue;
          if (Array.isArray(edge.sessions) && !edge.sessions.includes(sid)) continue;
          const answer = nodes.get(edge.from);
          const provenance = answer.provenance.find(item => item.session_id === sid &&
            (context ? context.analysis.relations.some(relation => relation.type === "answers" &&
              relation.from === item.source_node && relation.to === source.source_node) :
              sources.filter(item=>item.session_id===sid).length===1 && answer.provenance.filter(item=>item.session_id===sid).length===1));
          if (!provenance) continue;
          const existing = answers.find(item => item.nodeId === answer.id);
          if (existing) { existing.edgeIds.push(edge.id); continue; }
          answers.push({nodeId:answer.id,source:provenance,edgeIds:[edge.id],anchor:anchorsFor(provenance,"assistant")[0] || null});
        }
        answers.sort((a,b) => (a.anchor?.index ?? Infinity)-(b.anchor?.index ?? Infinity));
        // These are saved, rechecked assistant quotations on an answered
        // question, not inferred answers or new graph nodes/relationships.
        const evidenceByMessage=new Map();
        if(question.classification==="answered_question" && question.status==="answered") {
          for(const evidence of context?.analysis.assistantEvidence?.[source.source_node] || []) {
            const message=messages.get(evidence.messageId);
            if(!message || message.role!=="assistant" || message.index!==evidence.index ||
              typeof evidence.sourceMessageId!=="string" || !evidence.sourceMessageId ||
              !(context.analysis.anchors[source.source_node] || []).some(anchor=>anchor.messageId===evidence.messageId && anchor.index===evidence.index && anchor.sourceMessageId===evidence.sourceMessageId) ||
              answers.some(answer=>answer.anchor?.messageId===evidence.messageId)) continue;
            const quotes=Array.isArray(evidence.quotes)?evidence.quotes.filter(quote=>typeof quote==="string" && quote):[];
            if(!quotes.length && !(Number.isInteger(evidence.evidenceCount) && evidence.evidenceCount>0)) continue;
            if(!evidenceByMessage.has(evidence.messageId)) evidenceByMessage.set(evidence.messageId,{
              messageId:evidence.messageId,index:evidence.index,sourceMessageId:evidence.sourceMessageId,
              quotes:[],evidenceCount:0});
            const item=evidenceByMessage.get(evidence.messageId);
            item.quotes=[...new Set([...item.quotes,...quotes])];
            item.evidenceCount=Math.max(item.evidenceCount,Number.isInteger(evidence.evidenceCount)?evidence.evidenceCount:0,item.quotes.length);
          }
        }
        const answerEvidence=[...evidenceByMessage.values()].sort((a,b)=>a.index-b.index);
        const topicPath=structures.get(sid).path(source.source_node);
        group.entries.push({nodeId:question.id,source,anchor,topicPath,topicId:topicPath.at(-1) || "",
          timestamp:anchor ? messages.get(anchor.messageId).timestamp : "",answers,answerEvidence});
      }
    }
    for (const group of groups.values()) {
      group.entries.sort((a,b) => (a.anchor?.index ?? Infinity)-(b.anchor?.index ?? Infinity));
      const counts = new Map();
      for (const entry of group.entries) if(entry.anchor) counts.set(entry.anchor.index,(counts.get(entry.anchor.index) || 0)+1);
      for (const entry of group.entries) entry.sameMessage = !!entry.anchor && counts.get(entry.anchor.index)>1;
    }
    return [...groups.values()].sort((a,b) => {
      const first = group => {
        for(const entry of group.entries) {
          const stamp=entry.timestamp ? Date.parse(entry.timestamp) : NaN;
          if(Number.isFinite(stamp)) return stamp;
        }
        return Infinity;
      };
      return first(a)-first(b);
    });
  }
  function byTopic(group,selection="") {
    const selected=selection.startsWith("topic:") ? selection.slice(6) : "";
    const entries=group.entries.filter(entry=>!selection || (selection==="unassigned" ? !entry.topicId : entry.topicPath.includes(selected)));
    const topics=new Map(group.topics.map(topic=>[topic.id,topic]));
    const buckets=new Map();
    for(const entry of entries) {
      if(!buckets.has(entry.topicId)) {
        const topic=topics.get(entry.topicId);
        buckets.set(entry.topicId,{id:entry.topicId,title:topic ? topic.path.map(id=>topics.get(id)?.title || "제목 없는 주제").join(" › ") || topic.title : "주제 미확정",entries:[]});
      }
      buckets.get(entry.topicId).entries.push(entry);
    }
    return [...buckets.values()].sort((a,b)=>!a.id ? 1 : !b.id ? -1 :
      (a.entries[0].anchor?.index ?? Infinity)-(b.entries[0].anchor?.index ?? Infinity));
  }
  const occurrenceKey=entry=>JSON.stringify([entry.source.session_id,entry.source.source_node,entry.nodeId]);
  function orderedTopics(group) {
    const byId=new Map(group.topics.map(topic=>[topic.id,topic]));
    const choice=(id,value,title,entries)=>({id,value,title,count:entries.length,
      firstQuestionIndex:entries.reduce((first,entry)=>{
        const index=entry.anchor?.index;
        return Number.isInteger(index) && index>=0 && (first===null || index<first) ? index : first;
      },null)});
    const topics=group.topics.map(topic=>choice(topic.id,"topic:"+topic.id,
      topic.path.map(id=>byId.get(id)?.title).filter(Boolean).join(" › ") || topic.title,
      group.entries.filter(entry=>entry.topicPath.includes(topic.id))));
    for(const topic of topics) if(byId.get(topic.id)?.displayGroup==="auxiliary") topic.displayGroup="auxiliary";
    for(const topic of topics) if(topic.firstQuestionIndex===null && group.topicUserEvidence?.[topic.id])
      topic.userEvidence=group.topicUserEvidence[topic.id];
    const unassigned=group.entries.filter(entry=>!entry.topicId);
    if(unassigned.length) topics.push(choice("","unassigned","주제 미확정",unassigned));
    // Source positions remain usable without timestamps. Stable ties and
    // unconfirmed topics retain saved order; no source structure is changed.
    const position=topic=>topic.firstQuestionIndex ?? topic.userEvidence?.index ?? null;
    return topics.sort((a,b)=>position(a)===null ? (position(b)===null ? 0 : 1) :
      position(b)===null ? -1 : position(a)-position(b));
  }
  function topicsFor(group,scope="all") {
    return orderedTopics(group).filter(topic=>scope==="all" || (topic.displayGroup==="auxiliary") === (scope==="auxiliary"));
  }
  function scopedGroup(group,scope="all") {
    if(scope==="all") return group;
    const allowed=new Set(topicsFor(group,scope).map(topic=>topic.id));
    return {...group,entries:group.entries.filter(entry=>allowed.has(entry.topicId))};
  }
  function itemsForTopic(graph,group,selection) {
    const topicId=selection?.startsWith("topic:")?selection.slice(6):"", seen=new Set(), items=[];
    if(!topicId) return items;
    for(const node of graph.nodes) for(const source of node.provenance) {
      const key=JSON.stringify([node.id,source.session_id,source.source_node]);
      const path=group.itemPaths?.[source.source_node];
      if(source.session_id===group.sessionId && Array.isArray(path) && path.includes(topicId) && !seen.has(key)) {
        seen.add(key);items.push({nodeId:node.id,source});
      }
    }
    return items;
  }
  function mindBranch(group,topicSelection=null,questionKey="",scope="all") {
    if(!group) return {topics:[],questions:[],question:null,answers:[],answerEvidence:[]};
    const topics=topicsFor(group,scope), visible=scopedGroup(group,scope);
    const keys=new Set(topicSelection===null ? [] : byTopic(visible,topicSelection).flatMap(topic=>topic.entries.map(occurrenceKey)));
    const questions=group.entries.filter(entry=>keys.has(occurrenceKey(entry)));
    const question=questions.find(entry=>occurrenceKey(entry)===questionKey) || null;
    return {topics,questions,question,answers:question?.answers || [],answerEvidence:question?.answerEvidence || []};
  }
  function applyTopicDisplay(group,result) {
    const ids=new Set(group.topics.map(topic=>topic.id));
    const auxiliary=result?.auxiliaryTopicIds,primary=result?.primaryTopicIds;
    const names=result?.displayNames ?? {};
    if(result?.saved!==true || result.session!==group.sessionId ||
      result.topicDisplay?.sourceMapSha256!==group.topicDisplay.sourceMapSha256 ||
      !/^[a-f0-9]{64}$/.test(result.topicDisplay?.revision || "") ||
      !Array.isArray(auxiliary) || !Array.isArray(primary) ||
      [...auxiliary,...primary].some(id=>!ids.has(id)) ||
      new Set([...auxiliary,...primary]).size!==auxiliary.length+primary.length ||
      typeof names!=="object" || Array.isArray(names) ||
      Object.entries(names).some(([id,name])=>!ids.has(id) || typeof name!=="string" || !name || name!==name.trim() ||
        [...name].length>120 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(name)))throw new Error("Invalid display response");
    const groups=new Map([...auxiliary.map(id=>[id,"auxiliary"]),...primary.map(id=>[id,"primary"])]);
    const titles=new Map(Object.entries(names));
    const itemLabels=result.itemLabels ?? group.itemLabels ?? {},evidenceTitles=result.evidenceTitles ?? group.evidenceTitles ?? {};
    const itemIds=new Set(Object.keys(group.itemPaths || {}));
    const validAlias=value=>typeof value==="string" && value && value===value.trim() && [...value].length<=120 && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(value);
    if(typeof itemLabels!=="object" || Array.isArray(itemLabels) || Object.entries(itemLabels).some(([id,label])=>!itemIds.has(id) ||
      typeof label?.title!=="string" || (label.displayTitle!==undefined && !validAlias(label.displayTitle)) ||
      (group.itemLabels?.[id] && group.itemLabels[id].title!==label.title)) ||
      typeof evidenceTitles!=="object" || Array.isArray(evidenceTitles) || Object.entries(evidenceTitles).some(([id,values])=>!itemIds.has(id) ||
        !values || typeof values!=="object" || Array.isArray(values) || Object.entries(values).some(([mid,title])=>!mid || mid.length>128 || !validAlias(title))))throw new Error("Invalid item label response");
    return {...group,topicDisplay:result.topicDisplay,itemLabels,evidenceTitles,topics:group.topics.map(topic=>{
      const {displayGroup,displayTitle,...rest}=topic;
      const originalTitle=topic.originalTitle || topic.title;
      const path=topic.path.length?topic.path:[topic.id];
      const nearest=path.slice().reverse().map(id=>groups.get(id)).find(Boolean);
      return {...rest,originalTitle,title:titles.get(topic.id) || originalTitle,
        ...(titles.has(topic.id)?{displayTitle:titles.get(topic.id)}:{}),
        ...(nearest==="auxiliary"?{displayGroup:"auxiliary"}:{})};
    })};
  }
  return {build,isQuestion,byTopic,orderedTopics,topicsFor,scopedGroup,itemsForTopic,mindBranch,occurrenceKey,applyTopicDisplay,sessionTitle,applySessionDisplay};
})();
if(typeof module !== "undefined" && module.exports) module.exports = QuestionAnswers;

if(typeof document !== "undefined") (() => {
  "use strict";
  const data = JSON.parse(document.getElementById("graph-data").textContent);
  const ko = __KO_LABELS__;
  const byId = id => document.getElementById(id);
  const nodes = new Map(data.nodes.map(node => [node.id,node]));
  const adjacent = new Map(data.nodes.map(node => [node.id,[]]));
  for (const edge of data.edges) {
    adjacent.get(edge.from)?.push(edge);
    if (edge.to !== edge.from) adjacent.get(edge.to)?.push(edge);
  }
  let selected = null, mode = "mind", page = 0, neighborPage = 0, fit = false;
  let selectedSource=null,itemDisplayTarget=null,itemDisplaySaving=false,itemDisplayResult=null;
  const itemTitleDrafts=new Map();
  if(new URLSearchParams(location.search).get("mode")==="mind") mode="mind";
  const savedContext=JSON.parse(byId("qa-context").textContent);
  const liveCatalog=/^https?:$/.test(location.protocol);
  let qaGroups = QuestionAnswers.build(data,savedContext), qaLoading = true, qaFailed = 0;
  const qaFailedSessions=new Set();
  let qaReturn=null;
  try { qaReturn=JSON.parse(sessionStorage.getItem("context-graph-qa-return") || "null"); } catch (_) {}
  let qaSession="",qaTopic="",qaCatalog=Object.entries(savedContext).map(([id,context])=>({id,...context.session}));
  let catalogReady=!liveCatalog,catalogTimer=0,catalogRequest=null,catalogStopped=false,catalogSignature="",catalogEpoch=0;
  try { const state=JSON.parse(sessionStorage.getItem("context-graph-qa-filter") || "{}");qaSession=state.session || "";qaTopic=state.topic || ""; } catch (_) {}
  let mindSession="",mindTopic=null,mindQuestion="",mindPages=[0,0,0,0],mindTopicGroup="primary";
  let topicDisplaySaving=false,topicDisplayResult=null;
  const topicNameDrafts=new Map();
  const sessionNameDrafts=new Map();
  const sessionTitle=QuestionAnswers.sessionTitle;
  const sessionSearchText=session=>[sessionTitle(session),session.title,session.project,session.id,
    session.provider==='claude'?'Claude Code':'Codex',
    session.requestPreviews?.first?.text,session.requestPreviews?.latest?.text].filter(Boolean).join(" ").toLocaleLowerCase();
  const activityValue=session=>{const value=Date.parse(session.lastMessageAt || "");return Number.isFinite(value)?value:-Infinity;};
  const recentSessions=(a,b)=>{const left=activityValue(a),right=activityValue(b);return left===right?0:left<right?1:-1;};
  let sessionNameSaving=false,sessionNameResult=null;
  let catalogKind="primary",catalogParent="";
  try {const scope=JSON.parse(sessionStorage.getItem("context-graph-session-scope") || "{}");if(["primary","subagent","unknown"].includes(scope.kind)) {catalogKind=scope.kind;catalogParent=typeof scope.parent==="string"?scope.parent:"";}} catch (_) {}
  let restoredMindFilters=null;
  let mindDrawFrame=0,mindReveal=null;
  let sourcePanelWasOpen=false;
  const mindHosts=["mind-sessions","mind-topics","mind-questions","mind-answers"];
  // Sessions without analysis show three columns (session → request → response); the topic column waits for analysis.
  const stageLabels={analysed:[["03 / QUESTIONS","03","질문"],["04 / ANSWERS","04","답변"]],raw:[["02 / REQUESTS","02","요청"],["03 / RESPONSES","03","응답"]]};
  function rawSessionLayout(on) {
    byId("mind-canvas").classList.toggle("raw-session",on);byId("mind-stage-nav").classList.toggle("raw-session",on);
    for(const [index,[label,number,text]] of stageLabels[on?"raw":"analysed"].entries()) {
      const column=byId(mindHosts[index+2]).closest(".mind-column");if(column)column.dataset.stageLabel=label;
      const spans=byId("mind-step-"+(index+2)).querySelectorAll("span");if(spans[0])spans[0].textContent=number;if(spans[1])spans[1].textContent=text;
    }
  }
  const mindMotionQuery=window.matchMedia("(prefers-reduced-motion: reduce)");
  const mindMotionControl=byId("mind-reduce-motion");
  try {mindMotionControl.checked=sessionStorage.getItem("context-graph-reduced-motion")==="true";} catch (_) {}
  try {const state=JSON.parse(sessionStorage.getItem("context-graph-mind-selection") || "{}");mindSession=state.session || "";if(state.topicGroup==="auxiliary")mindTopicGroup="auxiliary";mindTopic=state.topic ?? null;mindQuestion=state.question || "";if(Array.isArray(state.pages) && state.pages.length===4 && state.pages.every(value=>Number.isInteger(value)&&value>=0))mindPages=state.pages;if(state.filters && typeof state.filters.query==="string" && typeof state.filters.project==="string")restoredMindFilters=state.filters;} catch (_) {}
  let mindSessionListOpen=!mindSession;
  try {const state=JSON.parse(sessionStorage.getItem("context-graph-mind-selection") || "{}");if(typeof state.sessionListOpen==="boolean")mindSessionListOpen=state.sessionListOpen;} catch (_) {}
  const qaTime = new Intl.DateTimeFormat("ko-KR",{dateStyle:"short",timeStyle:"short"});
  let mindRestorePending=Boolean(mindSession);
  const size = 20, neighborSize = 8;
  const label = node => ko.classification[node.classification] || node.classification;
  const relation = edge => ko.relation[edge.type] || edge.type;
  const topicOrderLabel=topic=>`질문 ${topic.count}개 · ${topic.firstQuestionIndex!==null ? "첫 질문 원문 #"+(topic.firstQuestionIndex+1) :
    topic.userEvidence ? "사용자 근거 원문 #"+(topic.userEvidence.index+1) : "순서 미확정"}`;
  const sourceGroup=source=>qaGroups.find(group=>group.sessionId===source?.session_id);
  function nodeTitle(node,source=null) {
    if(source) {
      const label=sourceGroup(source)?.itemLabels?.[source.source_node];
      return label?.displayTitle || label?.title || node.title;
    }
    const titles=[...new Set(node.provenance.map(value=>nodeTitle(node,value)))];
    return titles.length===1?titles[0]:node.title;
  }
  const nodeSearchTitles=node=>[node.title,...node.provenance.flatMap(source=>[nodeTitle(node,source),sourceGroup(source)?.itemLabels?.[source.source_node]?.title])];
  function resolveSource(node,provided=null) {
    const sources=[...new Map(node.provenance.map(source=>[JSON.stringify([source.session_id,source.source_node]),source])).values()];
    if(provided)return sources.find(source=>source.session_id===provided.session_id && source.source_node===provided.source_node) || null;
    const sid=mode==="mind"?mindSession:mode==="qa"?qaSession:null;
    const local=sources.filter(source=>source.session_id===sid);
    return local.length===1?local[0]:sources.length===1?sources[0]:null;
  }
  const evidenceTitle=(entry,evidence)=>sourceGroup(entry.source)?.evidenceTitles?.[entry.source.source_node]?.[evidence.messageId] || `어시스턴트 원문 #${evidence.index+1}`;
  function itemTitleTarget(node,provided,label="항목") {
    const source=resolveSource(node,provided);if(!source)return null;
    const group=sourceGroup(source);
    return {source,nodeId:node.id,label,originalTitle:group?.itemLabels?.[source.source_node]?.title || node.title};
  }
  const itemTargetKey=target=>JSON.stringify([target.source.session_id,target.source.source_node,target.evidenceMessage || null]);
  function displayedTargetTitle(target) {
    const group=sourceGroup(target.source);
    return target.evidenceMessage ? group?.evidenceTitles?.[target.source.source_node]?.[target.evidenceMessage] || target.originalTitle :
      group?.itemLabels?.[target.source.source_node]?.displayTitle || target.originalTitle;
  }
  function renderItemTitleEditor() {
    const target=itemDisplayTarget;if(!target)return;
    const key=itemTargetKey(target),info=sourceGroup(target.source)?.topicDisplay || {},input=byId("item-title-input");
    const value=itemTitleDrafts.has(key)?itemTitleDrafts.get(key):displayedTargetTitle(target);
    if(input.value!==value)input.value=value;
    const disabled=itemDisplaySaving || !liveCatalog || qaLoading || info.status==="invalid" ||
      !/^[a-f0-9]{64}$/.test(info.sourceMapSha256 || "") || !/^[a-f0-9]{64}$/.test(info.revision || "");
    input.disabled=disabled;byId("item-title-save").disabled=disabled;
    byId("item-title-save").textContent=itemDisplaySaving?"저장 중…":"제목 저장";
    byId("item-title-reset").disabled=disabled || displayedTargetTitle(target)===target.originalTitle;
    byId("item-title-heading").textContent=target.label+" 표시 제목 수정";
    byId("item-title-original").textContent="원래 제목: "+target.originalTitle;
    byId("item-title-status").textContent=itemDisplayResult?.key===key?itemDisplayResult.text:
      !liveCatalog?"실행 중인 뷰어에서 표시 제목을 변경할 수 있습니다.":"";
  }
  function openItemTitleEditor(target) {
    if(!target)return;itemDisplayTarget=target;renderItemTitleEditor();
    const dialog=byId("item-title-dialog");if(!dialog.open)dialog.showModal();
  }
  async function saveItemTitle(reset=false) {
    if(itemDisplaySaving || !itemDisplayTarget || !liveCatalog || byId("item-title-save").disabled)return;
    const target=itemDisplayTarget,key=itemTargetKey(target),group=sourceGroup(target.source);
    let displayTitle=null;
    if(!reset) {
      const raw=byId("item-title-input").value;displayTitle=raw.trim();
      if(!displayTitle || [...displayTitle].length>120 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(raw)) {
        itemDisplayResult={key,text:"표시 제목을 줄바꿈 없이 1~120자로 입력해 주세요."};renderItemTitleEditor();return;
      }
      if(displayTitle===target.originalTitle)displayTitle=null;
    }
    itemDisplaySaving=true;itemDisplayResult=null;renderItemTitleEditor();
    try {
      const response=await fetch("item-display.json",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Context-Graph-Action":"item-display"},
        body:JSON.stringify({session:target.source.session_id,item:target.source.source_node,displayTitle,
          sourceMapSha256:group.topicDisplay.sourceMapSha256,revision:group.topicDisplay.revision,
          ...(target.evidenceMessage?{evidenceMessage:target.evidenceMessage}:{})})});
      if(!response.ok) {
        itemDisplayResult={key,text:response.status===409?"설정이나 분석이 변경되었습니다. 새로고침한 뒤 다시 시도해 주세요.":
          "저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};return;
      }
      const result=await response.json();
      if(result.item!==target.source.source_node || result.evidenceMessage!==(target.evidenceMessage || null) || result.displayTitle!==displayTitle)
        throw new Error("Invalid item title response");
      const updated=QuestionAnswers.applyTopicDisplay(group,result);
      qaGroups=qaGroups.map(value=>value.sessionId===group.sessionId?updated:value);itemTitleDrafts.delete(key);
      itemDisplayResult={key,text:displayTitle===null?"원래 제목으로 복원했습니다.":"표시 제목을 저장했습니다."};
      if(mode==="mind")rememberMind();
      if(selected)details();render();
    } catch (_) {itemDisplayResult={key,text:"저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};}
    finally {itemDisplaySaving=false;renderItemTitleEditor();}
  }
  function renderSessionEditor(prefix,session) {
    byId(prefix+"-session-editor").hidden=!session;
    if(!session)return;
    const input=byId(prefix+"-session-name"),info=session.sessionDisplay || {};
    const value=sessionNameDrafts.has(session.id)?sessionNameDrafts.get(session.id):sessionTitle(session);
    input.dataset.sessionKey=session.id;if(input.value!==value)input.value=value;
    const disabled=sessionNameSaving || !liveCatalog || !catalogReady || info.status==="invalid" || !/^[a-f0-9]{64}$/.test(info.revision || "");
    input.disabled=disabled;
    byId(prefix+"-session-name-save").disabled=disabled;
    byId(prefix+"-session-name-save").textContent=sessionNameSaving?"저장 중…":"이름 저장";
    byId(prefix+"-session-name-reset").disabled=disabled || !session.displayTitle;
    byId(prefix+"-session-original-name").textContent="원래 이름: "+(session.title || session.project || "세션");
    byId(prefix+"-session-name-status").textContent=sessionNameResult?.session===session.id?sessionNameResult.text:
      !liveCatalog?"실행 중인 뷰어에서 세션 표시 이름을 변경할 수 있습니다.":"";
  }
  async function editSessionName(prefix,reset=false) {
    if(sessionNameSaving || !liveCatalog || !catalogReady)return;
    const sid=prefix==="mind"?mindSession:qaSession,session=catalogRows().find(value=>value.id===sid);
    if(!session || byId(prefix+"-session-name-save").disabled || (reset && !session.displayTitle))return;
    let displayName=null;
    if(!reset) {
      const input=byId(prefix+"-session-name").value;displayName=input.trim();
      if(!displayName || [...displayName].length>120 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(input)) {
        sessionNameResult={session:sid,text:"표시 이름을 줄바꿈 없이 1~120자로 입력해 주세요."};render();return;
      }
      if(displayName===session.title)displayName=null;
    }
    sessionNameSaving=true;sessionNameResult=null;render();
    try {
      const response=await fetch("session-display.json",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Context-Graph-Action":"session-display"},
        body:JSON.stringify({session:sid,displayName,revision:session.sessionDisplay.revision})});
      if(!response.ok) {
        sessionNameResult={session:sid,text:response.status===409?"표시 이름이 변경되었습니다. 새로고침한 뒤 다시 시도해 주세요.":
          response.status===403?"현재 접속에서는 표시 이름을 저장할 수 없습니다.":"저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};return;
      }
      const result=await response.json();
      if(result.displayName!==displayName)throw new Error("Invalid session display response");
      const current=qaCatalog.find(value=>value.id===sid) || session;
      const updated=QuestionAnswers.applySessionDisplay(current,result);
      // An in-flight catalogue snapshot must not undo the acknowledged label.
      catalogEpoch++;catalogRequest?.abort();
      qaCatalog=qaCatalog.map(value=>value.id===sid?updated:value);
      qaGroups=qaGroups.map(group=>group.sessionId===sid?{...group,title:sessionTitle(updated)}:group);
      sessionNameDrafts.delete(sid);
      if(mode==="mind" && mindSession===sid) {
        const term=byId("query").value.trim().toLocaleLowerCase();
        if(term && !sessionSearchText(updated).includes(term)) {
          byId("query").value="";restoredMindFilters=null;
          const rows=scopedCatalog().slice().sort(recentSessions)
            .filter(value=>!byId("project").value || value.project===byId("project").value);
          mindPages[0]=Math.max(0,Math.floor(rows.findIndex(value=>value.id===sid)/8));
        }
        rememberMind();
      }
      sessionNameResult={session:sid,text:displayName===null?"원래 세션 이름으로 복원했습니다.":"세션 표시 이름을 저장했습니다."};
    } catch (_) {sessionNameResult={session:sid,text:"저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};}
    finally {sessionNameSaving=false;render();refreshCatalog();}
  }
  function renderTopicEditor(prefix,group,selection) {
    const topic=group?.topics.find(topic=>"topic:"+topic.id===selection),control=byId(prefix+"-topic-edit");
    byId(prefix+"-topic-editor").hidden=!topic;
    if(!topic)return;
    const info=group.topicDisplay || {};
    control.textContent=topicDisplaySaving?"저장 중…":topic.displayGroup==="auxiliary"?"기본 목록으로 복원":"보조 정보로 이동";
    control.disabled=topicDisplaySaving || qaLoading || !liveCatalog || info.status==="invalid" ||
      !/^[a-f0-9]{64}$/.test(info.sourceMapSha256 || "") || !/^[a-f0-9]{64}$/.test(info.revision || "");
    const key=JSON.stringify([group.sessionId,topic.id]),input=byId(prefix+"-topic-name");
    const value=topicNameDrafts.has(key)?topicNameDrafts.get(key):topic.title;
    input.dataset.topicKey=key;
    if(input.value!==value)input.value=value;
    input.disabled=control.disabled;
    byId(prefix+"-topic-name-save").disabled=control.disabled;
    byId(prefix+"-topic-name-reset").disabled=control.disabled || !topic.displayTitle;
    byId(prefix+"-topic-original-name").textContent="원래 이름: "+(topic.originalTitle || topic.title);
    const message=topicDisplayResult?.session===group.sessionId && topicDisplayResult?.topic===topic.id ? topicDisplayResult.text :
      !liveCatalog?"실행 중인 뷰어에서 표시 설정을 변경할 수 있습니다.":"";
    byId(prefix+"-topic-edit-status").textContent=message;
  }
  async function editTopicDisplay(prefix,operation="group") {
    if(topicDisplaySaving || !liveCatalog || qaLoading)return;
    const session=prefix==="mind"?mindSession:qaSession,selection=prefix==="mind"?mindTopic:qaTopic;
    const group=qaGroups.find(group=>group.sessionId===session),topic=group?.topics.find(topic=>"topic:"+topic.id===selection);
    if(!topic || byId(prefix+"-topic-edit").disabled)return;
    const target=topic.displayGroup==="auxiliary"?"primary":"auxiliary";
    const naming=operation!=="group",key=JSON.stringify([session,topic.id]);
    let displayName=null;
    if(operation==="name") {
      const input=byId(prefix+"-topic-name").value;
      displayName=input.trim();
      if(!displayName || [...displayName].length>120 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(input)) {
        topicDisplayResult={session,topic:topic.id,text:"표시 이름을 줄바꿈 없이 1~120자로 입력해 주세요."};render();return;
      }
      if(displayName===(topic.originalTitle || topic.title))displayName=null;
    }
    topicDisplaySaving=true;topicDisplayResult=null;render();
    try {
      const response=await fetch("topic-display.json",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json","X-Context-Graph-Action":"topic-display"},
        body:JSON.stringify({session,topic:topic.id,...(naming?{displayName}:{group:target}),sourceMapSha256:group.topicDisplay.sourceMapSha256,revision:group.topicDisplay.revision})});
      if(!response.ok) {
        topicDisplayResult={session,topic:topic.id,text:response.status===409?"설정이나 분석이 변경되었습니다. 새로고침한 뒤 다시 시도해 주세요.":
          response.status===403?"현재 접속에서는 표시 설정을 저장할 수 없습니다.":"저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};
        return;
      }
      const result=await response.json();
      if(result.topic!==topic.id || (naming?(result.operation!=="name" || result.displayName!==displayName):result.group!==target))throw new Error("Invalid display response");
      const updated=QuestionAnswers.applyTopicDisplay(group,result);
      qaGroups=qaGroups.map(value=>value.sessionId===session?updated:value);
      if(naming)topicNameDrafts.delete(key);
      if(!naming && mode==="mind" && mindSession===session && mindTopic===selection) {
        mindTopicGroup=target;mindQuestion="";mindPages[2]=mindPages[3]=0;
        mindPages[1]=Math.max(0,Math.floor(QuestionAnswers.topicsFor(updated,target).findIndex(value=>value.id===topic.id)/8));
        close();rememberMind();
      }
      topicDisplayResult={session,topic:topic.id,text:naming?(displayName===null?"원래 이름으로 복원했습니다.":"표시 이름을 저장했습니다."):
        target==="auxiliary"?"보조 정보로 이동했습니다. 설정이 저장되었습니다.":"기본 목록으로 복원했습니다. 설정이 저장되었습니다."};
    } catch (_) {
      topicDisplayResult={session,topic:topic.id,text:"저장 결과를 확인할 수 없습니다. 새로고침해 확인해 주세요."};
    } finally {topicDisplaySaving=false;render();}
  }
  function el(tag,text="",className="") {
    const node = document.createElement(tag); node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function button(text,action,className="") {
    const node = el("button",text,className); node.type="button"; node.addEventListener("click",action); return node;
  }
  function managementMenu(label,control) {
    const menu=el("details","","node-management inline-management"),toggle=el("summary","⋯");
    toggle.setAttribute("aria-label",label);toggle.title=label;
    const content=el("div","","node-management-content");content.append(control);menu.append(toggle,content);return menu;
  }
  // Old offline exports have no lineage fields. Explicit unknown metadata is
  // kept separate; absent legacy fields remain readable as ordinary sessions.
  const sessionKind=session=>["primary","subagent","unknown"].includes(session.sessionKind)?session.sessionKind:"primary";
  const parentKey=session=>[session.groupSessionId,session.rootSessionId,session.parentSessionId].find(id=>id && id!==session.id) || "unknown-parent";
  function rememberScope() {
    try {sessionStorage.setItem("context-graph-session-scope",JSON.stringify({kind:catalogKind,parent:catalogParent}));} catch (_) {}
  }
  function catalogRows() {
    return qaCatalog.length ? qaCatalog : qaGroups.map(group=>({id:group.sessionId,title:group.title,project:group.project,hasAnalysis:true}));
  }
  function replaceOptions(control,options,value) {
    const signature=JSON.stringify(options);
    if(control.dataset.options!==signature) {
      control.replaceChildren();
      for(const [key,title] of options) {const option=el("option",title);option.value=key;control.append(option);}
      control.dataset.options=signature;
    }
    control.value=value;
  }
  function updateCatalogScope() {
    const rows=catalogRows(),labels={primary:"일반 세션",subagent:"보조 작업",unknown:"분류 미확정"};
    replaceOptions(byId("catalog-kind"),Object.entries(labels).map(([key,title])=>[key,`${title} · ${rows.filter(session=>sessionKind(session)===key).length}개`]),catalogKind);
    const groups=new Map();
    for(const session of rows.filter(session=>sessionKind(session)==="subagent")) {
      const key=parentKey(session);groups.set(key,(groups.get(key)||0)+1);
    }
    if(catalogReady && !groups.has(catalogParent)) catalogParent=groups.keys().next().value || "";
    const choices=[...groups].map(([key,count])=>{
      const parent=rows.find(session=>session.id===key);
      return [key,`${parent?.displayTitle || parent?.title || (key==="unknown-parent"?"상위 세션 미확정":"수집되지 않은 상위 세션 · "+key.slice(0,8))} · 보조 작업 ${count}개`];
    });
    replaceOptions(byId("catalog-parent"),choices,catalogParent);
    byId("catalog-parent").disabled=!choices.length;
    byId("catalog-parent-filter").hidden=catalogKind!=="subagent";
    byId("catalog-scope-hint").textContent=catalogKind==="primary" ? "직접 시작한 세션입니다. 위의 보조 작업에서 에이전트 작업을 상위 세션별로 볼 수 있습니다." : catalogKind==="subagent" ? "선택한 상위 세션에서 파생된 보조 작업입니다. 제목은 확인된 분기 후 요청을 사용합니다." : "종류를 확인할 수 없는 기록입니다. 원문은 그대로 열어 볼 수 있습니다.";
  }
  function scopedCatalog() {
    updateCatalogScope();
    return catalogRows().filter(session=>sessionKind(session)===catalogKind && (catalogKind!=="subagent" || parentKey(session)===catalogParent));
  }
  function filtered() {
    const term = byId("query").value.trim().toLocaleLowerCase();
    return data.nodes.filter(node => (!byId("project").value || node.project === byId("project").value) &&
      (!byId("category").value || node.classification === byId("category").value) &&
      (!term || [...nodeSearchTitles(node),node.summary,node.project,label(node)].join(" ").toLocaleLowerCase().includes(term)));
  }
  function card(node) {
    const item = button("",()=>choose(node.id),"card"+(selected===node.id?" selected":""));
    item.dataset.nodeId = node.id;
    item.setAttribute("aria-pressed",String(selected===node.id));
    item.append(el("span",[label(node),ko.status[node.status]].filter(Boolean).join(" · "),"pill"),
      el("strong",nodeTitle(node),"name"),el("span",node.summary || "요약 없음","excerpt"));
    return item;
  }
  async function openSource(provenance,control,sourceMessageId="") {
    if(mode==="mind") rememberMind();
    control.disabled=true;
    try {
      const query=new URLSearchParams({session:provenance.session_id,limit:"1"});
      const response=await fetch("timeline/messages.json?"+query,{cache:"no-store"});
      if(!response.ok) throw new Error("Unavailable");
      const view=await response.json();
      const anchors=view.analysis.anchors[provenance.source_node] || [];
      const anchor=sourceMessageId ? anchors.find(item=>item.sourceMessageId===sourceMessageId) : anchors[0];
      if(!anchor) { control.textContent="원문 연결 시점 미확정"; return; }
      const target=new URL("./",location.href);
      target.search=new URLSearchParams({view:"timeline",session:provenance.session_id,message:anchor.messageId}).toString();
      if(window.parent!==window) window.parent.postMessage({type:"context-graph-open-source",session:provenance.session_id,message:anchor.messageId},location.origin);
      else location.assign(target.href);
    } catch (_) { control.textContent="원문을 열지 못했습니다 · 다시 시도"; }
    finally { control.disabled=false; }
  }
  function updateProjectOptions() {
    const control=byId("project"),value=restoredMindFilters?.project ?? control.value;
    const projects=[...new Set([...data.projects,...qaCatalog.map(session=>session.project).filter(Boolean)])].sort();
    // Retain the existing all-projects option and a pending restored filter
    // until the first authoritative catalogue arrives.
    if(!catalogReady && value && !projects.includes(value)) projects.push(value);
    if(JSON.stringify([...control.options].slice(1).map(option=>option.value))!==JSON.stringify(projects)) {
      for(const option of [...control.options].slice(1)) option.remove();
      for(const project of projects) {const option=el("option",project);option.value=project;control.append(option);}
    }
    control.value=projects.includes(value)?value:"";
    if(catalogReady) restoredMindFilters=null;
  }
  async function refreshCatalog() {
    if(!liveCatalog || catalogStopped || catalogRequest) return;
    clearTimeout(catalogTimer);
    const request=new AbortController(),epoch=catalogEpoch;catalogRequest=request;
    const timeout=setTimeout(()=>request.abort(),8000);
    try {
      const response=await fetch("timeline/sessions.json",{cache:"no-store",signal:request.signal});
      if(!response.ok) throw new Error("Unavailable");
      const result=await response.json();
      if(!Array.isArray(result.sessions) || !result.sessions.every(session=>session && typeof session.id==="string")) throw new Error("Invalid catalogue");
      if(catalogStopped || request.signal.aborted || epoch!==catalogEpoch) return;
      const signature=JSON.stringify(result.sessions);
      if(!catalogReady || signature!==catalogSignature) {
        const initial=!catalogReady;
        qaCatalog=result.sessions;catalogSignature=signature;catalogReady=true;
        updateProjectOptions();
        // New raw sessions do not change the saved analysis. Keep the current
        // branch DOM and its motion/focus intact while updating the catalogue.
        if(mode==="mind") renderMind(null,!initial);
        else if(mode==="qa") renderQuestionAnswers();
      }
    } catch (_) {
      // Keep the last good catalogue and selection; the next poll retries.
    } finally {
      clearTimeout(timeout);
      if(catalogRequest===request) catalogRequest=null;
      if(!catalogStopped) catalogTimer=setTimeout(refreshCatalog,5000);
    }
  }
  async function loadQuestionOrder() {
    qaFailed=0;qaFailedSessions.clear();
    const contexts = {...savedContext};
    const sessions = [...new Set(data.nodes.filter(QuestionAnswers.isQuestion).flatMap(node=>node.provenance.map(item=>item.session_id)))];
    if(!liveCatalog) { qaLoading=false;render();return; }
    async function readSession(session) {
      const result = await fetch("timeline/context.json?"+new URLSearchParams({session}),{cache:"no-store"});
      if(!result.ok) throw new Error("Unavailable");
      const first = await result.json();
      if(first.session?.id!==session || !Array.isArray(first.messages))throw new Error('Invalid context');
      // Keep only metadata needed to order rechecked anchors, never copy raw text.
      const metadata = rows => rows.map(({id,index,role,timestamp,hasUserContent})=>({id,index,role,timestamp,hasUserContent}));
      const fallback=savedContext[session];
      const structure=first.analysis.structure || (fallback?.session.importedAt===first.session.importedAt ? fallback?.analysis.structure : []) || [];
      const context = {session:first.session,analysis:{...first.analysis,structure},messages:metadata(first.messages)};
      contexts[session] = context;
    }
    // Bound concurrent local reads even when many sessions have been analysed.
    const pending=sessions.slice();
    await Promise.all(Array.from({length:Math.min(2,pending.length)},async()=>{
      while(pending.length) {
        const session=pending.shift();
        try { await readSession(session); }
        catch (_) { qaFailed++;qaFailedSessions.add(session); }
      }
    }));
    qaGroups=QuestionAnswers.build(data,contexts);qaLoading=false;render();
  }
  function saveQuestionFilter() {
    rememberScope();
    try {sessionStorage.setItem("context-graph-qa-filter",JSON.stringify({session:qaSession,topic:qaTopic}));} catch (_) {}
  }
  function sessionPicker() {
    const catalog=scopedCatalog();
    const available=catalog.filter(session=>!byId("project").value || session.project===byId("project").value);
    if(!available.some(session=>session.id===qaSession)) {
      qaSession=available.find(session=>session.id===qaReturn?.session)?.id || available.find(session=>qaGroups.some(group=>group.sessionId===session.id))?.id || available[0]?.id || "";
      qaTopic="";
    }
    const sessionControl=byId("qa-session");sessionControl.replaceChildren();
    for(const session of available) {
      const option=el("option",`${sessionTitle(session)} · ${session.id.slice(0,8)}${session.hasAnalysis===false?" · 분석 대기":""}`);
      option.value=session.id;sessionControl.append(option);
    }
    sessionControl.value=qaSession;sessionControl.disabled=!available.length;
    const session=available.find(session=>session.id===qaSession);
    renderSessionEditor("qa",session);
    const group=qaGroups.find(group=>group.sessionId===qaSession) || {sessionId:qaSession,title:session?.title || "",topics:[],entries:[]};
    const topicControl=byId("qa-topic");topicControl.replaceChildren();
    const primaryGroup=QuestionAnswers.scopedGroup(group,"primary");
    const all=el("option",`기본 주제 전체 · 질문 ${primaryGroup.entries.length}개`);all.value="";topicControl.append(all);
    const topics=QuestionAnswers.orderedTopics(group);
    const auxiliary=el("optgroup");auxiliary.setAttribute("label",`보조 정보 ${topics.filter(topic=>topic.displayGroup==="auxiliary").length}개`);
    for(const topic of topics) {
      const option=el("option",`${topic.title} · ${topicOrderLabel(topic)}`);
      option.value=topic.value;(topic.displayGroup==="auxiliary"?auxiliary:topicControl).append(option);
    }
    if(auxiliary.children.length)topicControl.append(auxiliary);
    if(![...topicControl.options].some(option=>option.value===qaTopic)) qaTopic="";
    topicControl.value=qaTopic;topicControl.disabled=!session || !topics.length;
    renderTopicEditor("qa",group,qaTopic);
    byId("qa-session-meta").textContent=session ? `선택한 세션: ${sessionTitle(session)}${session.messageCount!==undefined?" · 원문 메시지 "+session.messageCount+"개":""} · 주제 ${topics.length-auxiliary.children.length}개${auxiliary.children.length?" · 보조 정보 "+auxiliary.children.length+"개":""} · ${group.entries.length}개 질문${assignmentNotice(session)}` : "선택할 수 있는 세션이 없습니다.";
    byId("qa-session-raw").disabled=!session || !/^https?:$/.test(location.protocol);
    saveQuestionFilter();
    const auxiliarySelected=topics.find(topic=>topic.value===qaTopic)?.displayGroup==="auxiliary";
    return {group:auxiliarySelected?QuestionAnswers.scopedGroup(group,"auxiliary"):primaryGroup,session,auxiliarySelected};
  }
  function openSession(session=qaSession) {
    if(!session) return;
    if(mode==="mind") rememberMind();
    if(window.parent!==window) window.parent.postMessage({type:"context-graph-open-session",session},location.origin);
    else location.assign("./?"+new URLSearchParams({view:"timeline",session}));
  }
  function assignmentNotice(session) {
    const assignment=session?.topicAssignment;
    if(assignment?.status==="decisions") return ` · Decisions 주제 배정: ${assignment.assigned}개 · 검토 필요 ${assignment.review}개 · 새 주제 필요 ${assignment.newTopic}개 (미확정 항목은 주제 미확정에서 확인)`;
    if(assignment?.status==="stale") return " · 주제 배정 후 원본 분석이 변경되어 기존 Codex 주제를 표시합니다.";
    if(assignment?.status==="invalid") return " · 저장된 주제 배정을 확인할 수 없어 기존 Codex 주제를 표시합니다.";
    return "";
  }
  function rememberMind() {
    rememberScope();
    try {sessionStorage.setItem("context-graph-mind-selection",JSON.stringify({session:mindSession,topic:mindTopic,question:mindQuestion,pages:mindPages,...(mindSessionListOpen || !mindSession?{sessionListOpen:mindSessionListOpen}:{}),...(mindTopicGroup==="auxiliary"?{topicGroup:"auxiliary"}:{}),filters:{query:byId("query").value,project:byId("project").value}}));sessionStorage.setItem("context-graph-reading-mode","mind");} catch (_) {}
  }
  function mindCard(title,meta,active,action,kind,key) {
    const card=button("",action,"mind-node");card.setAttribute("aria-pressed",String(active));
    card.dataset.kind=kind;card.dataset.key=key;card.title=title;
    card.append(el("strong",title,"name"),el("span",meta,"mind-meta"));return card;
  }
  function mindSessionCard(session,active,action) {
    const title=sessionTitle(session);
    const card=mindCard(title,"",active,action,"session",session.id);
    const heading=el("span","","session-heading");
    heading.append(el("strong",title,"name"));
    if(session.displayTitle) heading.append(el("span","표시 이름","session-title-source"));
    else if(session.titleSource==="first_user") heading.append(el("span","첫 요청","session-title-source"));
    else if(session.titleSource==="own_request") heading.append(el("span","분기 후 요청","session-title-source"));
    const stamp=activityValue(session);
    const activity=Number.isFinite(stamp) ? `최근 ${qaTime.format(new Date(stamp))}` : "활동 시각 미확정";
    card.title=`${title}\n세션 ID: ${session.id}`;
    card.classList.add("mind-session-node");
    const identity=el("span","","session-identity");
    identity.append(heading,
      el("span",`${session.provider==='claude'?'Claude Code':'Codex'} · ${session.project || "프로젝트 미확정"}`,"session-project"),
      el("span",activity,"mind-activity"),
      el("span",`${session.hasAnalysis?"분석 있음":"분석 대기"}${session.messageCount!==undefined?" · 메시지 "+session.messageCount+"개":""}`,"mind-meta"));
    if(catalogRows().filter(item=>sessionTitle(item)===title).length>1)identity.append(el("span",`같은 제목 구분 · ${session.id.slice(-8)}`,"mind-activity session-key"));
    const helpers=catalogRows().filter(item=>sessionKind(item)==="subagent" && parentKey(item)===session.id).length;
    if(sessionKind(session)==="primary" && helpers) identity.append(el("span",`보조 작업 ${helpers}개` ,"mind-activity"));
    card.replaceChildren(identity);
    if(!mindSessionListOpen)return card;
    const previews=session.requestPreviews;
    for(const [key,title] of [["first",previews?.scope==="own"?"분기 후 첫 요청":"첫 요청"],["latest","최근 요청"]]) {
      const entry=previews?.[key],box=el("span","","session-request-preview");box.dataset.preview=key;
      const missing=previews?.scope==="unconfirmed"?"이 세션의 고유 요청을 확인할 수 없습니다.":previews?"확인할 요청이 없습니다.":"실시간 뷰어에서 원문 미리보기를 확인할 수 있습니다.";
      box.append(el("span",title,"session-preview-label"),el("span",entry?.text || missing,"session-preview-text"));
      if(entry?.messageId)box.dataset.messageId=entry.messageId;
      card.append(box);
    }
    return card;
  }
  function answerEvidenceCard(entry,evidence,mind=false) {
    const live=/^https?:$/.test(location.protocol);
    const card=button("",()=>{
      if(!live) return choose(entry.nodeId);
      if(mode==="qa") try {sessionStorage.setItem("context-graph-qa-return",JSON.stringify({node:entry.nodeId,session:entry.source.session_id}));} catch (_) {}
      if(mind){explorer.openReader(entry.source.session_id,evidence.messageId);return;}
      return openSource(entry.source,card,evidence.sourceMessageId);
    },
      (mind?"mind-node ":"")+"answer-evidence-card");
    card.dataset.kind="answer-evidence";card.dataset.key=JSON.stringify([entry.source.session_id,entry.source.source_node,evidence.messageId]);
    card.dataset.messageId=evidence.messageId;
    const title=evidenceTitle(entry,evidence),originalTitle=`어시스턴트 원문 #${evidence.index+1}`;
    card.append(el("span","저장된 답변 근거","qa-label"),el("strong",title,"name"),
      el("span",`${title!==originalTitle?originalTitle+" · ":""}검증된 인용 ${evidence.evidenceCount}개`,"mind-meta"));
    if(evidence.quotes.length) card.append(el("span",evidence.quotes.join("\n\n"),"answer-evidence-preview"));
    else card.append(el("span","인용 내용은 질문·근거 보기에서 확인할 수 있습니다.","mind-meta"));
    card.append(el("span",live?"답변 원문 읽기 →":"질문·근거 보기 →","qa-read"));
    const wrapper=el("div","","answer-evidence-entry");
    const edit=button("답변 표시 제목 수정",()=>openItemTitleEditor({source:entry.source,nodeId:entry.nodeId,
      label:"답변 근거",evidenceMessage:evidence.messageId,originalTitle}),"item-title-trigger");
    wrapper.append(card,mind?managementMenu("답변 근거 관리",edit):edit);
    return wrapper;
  }
  function mindPage(host,items,index,makeCard,empty,groupLabel=null) {
    const previousList=host.querySelector(".mind-node-list"),pageSize=8;
    const owner=JSON.stringify([mindSessionListOpen,index===0?[catalogKind,catalogParent,byId("query").value,byId("project").value]:
      index===1?[mindSession,mindTopicGroup]:index===2?[mindSession,mindTopic,mindTopicGroup]:[mindSession,mindTopic,mindQuestion]]);
    const ready=index===0 ? catalogReady : !qaLoading && !qaFailedSessions.has(mindSession);
    if(ready) mindPages[index]=Math.min(mindPages[index],Math.max(0,Math.ceil(items.length/pageSize)-1));
    const keepScroll=previousList?.dataset.owner===owner && previousList.dataset.page===String(mindPages[index]) && previousList.querySelector(".mind-node");
    const scrollTop=keepScroll?previousList.scrollTop:0;
    host.replaceChildren();const list=el("div","","mind-node-list");
    list.dataset.owner=owner;list.dataset.page=String(mindPages[index]);host.append(list);
    list.addEventListener("scroll",()=>scheduleMindDraw());
    let previousGroup=null;
    for(const item of items.slice(mindPages[index]*pageSize,(mindPages[index]+1)*pageSize)) {
      const group=groupLabel?.(item);
      if(group && group!==previousGroup) list.append(el("h3",group,"mind-answer-group"));
      list.append(makeCard(item));previousGroup=group;
    }
    if(!items.length) list.append(el("p",empty,"mind-empty"));
    list.scrollTop=scrollTop;
    const active=list.querySelector('.mind-node[aria-pressed="true"]');
    if(active && (!keepScroll || previousList.dataset.selectedKey!==active.dataset.key))
      list.scrollTop=Math.max(0,active.offsetTop-Math.max(0,(list.clientHeight-active.offsetHeight)/2));
    list.dataset.selectedKey=active?.dataset.key || "";
    if(items.length>pageSize) {
      const paging=el("div","","paging"),previous=button("이전",()=>{mindPages[index]--;renderMind(index);rememberMind();}),next=button("다음",()=>{mindPages[index]++;renderMind(index);rememberMind();});
      const names=["세션","주제",mindTopicGroup==="auxiliary"?"보조 항목":"질문","답변"];
      previous.setAttribute("aria-label",`이전 ${names[index]} 노드`);next.setAttribute("aria-label",`다음 ${names[index]} 노드`);
      previous.disabled=mindPages[index]===0;next.disabled=(mindPages[index]+1)*pageSize>=items.length;
      paging.append(previous,el("span",`${mindPages[index]*pageSize+1}–${Math.min((mindPages[index]+1)*pageSize,items.length)} / ${items.length}`,"hint"),next);host.append(paging);
    }
  }
  function mindBox(element,canvas) {
    const rect=element.getBoundingClientRect(),base=canvas.getBoundingClientRect();
    return {x:rect.left-base.left,y:rect.top-base.top,width:rect.width,height:rect.height};
  }
  function visibleMindNode(element) {
    const list=element.closest(".mind-node-list");if(!list)return true;
    const node=element.getBoundingClientRect(),clip=list.getBoundingClientRect(),center=node.top+node.height/2;
    return node.width>0 && node.height>0 && center>=clip.top && center<=clip.bottom;
  }
  function syncMindMotion() {
    document.body.classList.toggle("mind-motion-off",mindMotionQuery.matches || mindMotionControl.checked);
    for(const element of byId("mind-canvas").querySelectorAll(".mind-node-reveal,.mind-edge-reveal")) {
      element.classList.remove("mind-node-reveal","mind-edge-reveal");element.style.removeProperty("--mind-delay");
    }
    mindReveal=null;
    scheduleMindDraw();
  }
  function scheduleMindDraw(animateColumn=null) {
    cancelAnimationFrame(mindDrawFrame);
    // A requested reveal is remembered rather than tied to one frame: list scrolls, detail toggles
    // and data arrival reschedule the draw before that frame runs, and would otherwise drop it.
    if(Number.isInteger(animateColumn))mindReveal={column:animateColumn,at:Date.now(),startedAt:null};
    mindDrawFrame=requestAnimationFrame(()=>{mindDrawFrame=0;drawMindLinks();});
  }
  function drawMindLinks() {
    if(mode!=="mind")return;
    const reduced=mindMotionQuery.matches || mindMotionControl.checked;
    // A reveal waits up to 3s for its column to get nodes (session data may still be loading), then lives 1s.
    if(mindReveal && (reduced || Date.now()-(mindReveal.startedAt ?? mindReveal.at)>(mindReveal.startedAt?1000:3000)))mindReveal=null;
    // Redraws during a live reveal re-apply it with a negative delay so the animation continues in place.
    const reveal=mindReveal,elapsed=reveal?.startedAt?Date.now()-reveal.startedAt:0;
    const canvas=byId("mind-canvas"),svg=byId("mind-links");svg.replaceChildren();
    if(mindSessionListOpen || !mindSession)return;
    svg.setAttribute("viewBox",`0 0 ${canvas.clientWidth} ${canvas.clientHeight}`);
    const raw=canvas.classList.contains("raw-session"),columns=raw?[0,2,3]:[0,1,2,3];
    const revealColumn=reveal?(raw && reveal.column===1?2:reveal.column):null;
    for(let step=1;step<columns.length;step++) {
      const column=columns[step],fromId=mindHosts[columns[step-1]],toId=mindHosts[column];
      const from=byId(fromId).querySelector('.mind-node[aria-pressed="true"]');if(!from || !visibleMindNode(from))continue;
      const a=mindBox(from,canvas);
      for(const [index,to] of [...byId(toId).querySelectorAll(".mind-node")].filter(visibleMindNode).entries()) {
        const b=mindBox(to,canvas),x1=a.x+a.width,y1=a.y+a.height/2,x2=b.x,y2=b.y+b.height/2,mid=(x1+x2)/2;
        const evidence=to.dataset.kind==="answer-evidence";
        const line=`M ${x1} ${y1} C ${mid} ${y1},${mid} ${y2},${x2} ${y2}`;
        const focused=to.getAttribute("aria-pressed")==="true" || column===3 && byId(toId).querySelectorAll('.mind-node[aria-pressed="true"]').length===0;
        const path=svgEl("path",{d:line,class:"mind-edge"+(focused?" mind-edge-active":"")+(evidence?" mind-evidence-edge":""),pathLength:1,"data-column":column});
        if(reveal && column===revealColumn && !evidence) {path.classList.add("mind-edge-reveal");path.style.setProperty("--mind-delay",`${index*38-elapsed}ms`);}
        svg.append(path);
      }
    }
    if(reveal) {
      const nodes=[...byId(mindHosts[revealColumn]).querySelectorAll(".mind-node")];
      for(const [index,node] of nodes.entries()) {
        if(node.classList.contains("mind-node-reveal"))continue;
        node.style.setProperty("--mind-delay",`${130+index*38-elapsed}ms`);node.classList.add("mind-node-reveal");
      }
      if(reveal.startedAt===null && nodes.length)reveal.startedAt=Date.now();
    }
  }
  function renderMind(animateColumn=null,catalogOnly=false) {
    cancelAnimationFrame(mindDrawFrame);
    const active=document.activeElement;
    const focusHost=mindHosts.find(id=>active?.closest("#"+id));
    const focus=focusHost ? {kind:active.dataset.kind,key:active.dataset.key,
      label:active.getAttribute("aria-label"),host:focusHost} : null;
    const term=byId("query").value.trim().toLocaleLowerCase(),project=byId("project").value;
    const catalog=scopedCatalog().slice()
      .sort(recentSessions)
      .filter(session=>(!project || session.project===project) && (!term || sessionSearchText(session).includes(term)));
    if(catalogReady && mindSession && !catalog.some(session=>session.id===mindSession)) {mindSession="";mindTopic=null;mindQuestion="";mindPages[1]=mindPages[2]=mindPages[3]=0;catalogOnly=false;}
    const session=catalog.find(session=>session.id===mindSession),group=qaGroups.find(group=>group.sessionId===mindSession);
    explorer.updateSession(session?.id || "");
    byId('explorer-analyze').disabled=!session || !liveCatalog || window.parent===window;
    byId('mind-canvas').classList.remove('raw-chronology');rawSessionLayout(false);
    byId('mind-answers-title').textContent='답변 · 근거';
    syncSessionViewControls();
    byId("session-mindmap").setAttribute("aria-label",mindSessionListOpen?"세션 목록":"세션 마인드맵");
    byId("mind-canvas").classList.toggle("session-picker-open",mindSessionListOpen);
    byId("mind-sessions").hidden=false;
    byId("mind-session-heading").textContent=mindSessionListOpen?"세션 선택":"세션";
    byId("mind-session-list-info").textContent=mindSessionListOpen?`${catalog.length}개 · 최근 대화순 · 원문 미리보기`:"";
    byId("mind-session-list-info").hidden=!mindSessionListOpen;
    byId("mind-session-change").hidden=mindSessionListOpen && !session;
    byId("mind-session-change").textContent=mindSessionListOpen?"마인드맵으로 돌아가기":"세션 목록 펼치기";
    for(const host of mindHosts.slice(1))byId(host).closest(".mind-column").hidden=mindSessionListOpen;
    byId("mind-actions").hidden=mindSessionListOpen;
    renderSessionEditor("mind",session);
    const allTopics=group?QuestionAnswers.orderedTopics(group):[];
    const auxiliaryMode=mindTopicGroup==="auxiliary";
    const auxiliaryCount=allTopics.filter(topic=>topic.displayGroup==="auxiliary").length;
    byId("mind-topic-groups").hidden=!auxiliaryCount;
    byId("mind-primary-topics").textContent=`주제 ${allTopics.length-auxiliaryCount}개`;
    byId("mind-auxiliary-topics").textContent=`보조 정보 ${auxiliaryCount}개`;
    byId("mind-primary-topics").setAttribute("aria-pressed",String(!auxiliaryMode));
    byId("mind-auxiliary-topics").setAttribute("aria-pressed",String(auxiliaryMode));
    byId("mind-canvas").classList.toggle("auxiliary-topics",auxiliaryMode);
    byId("mind-topics-title").textContent=auxiliaryMode?"보조 정보":"주제 · 원문순";
    byId("mind-questions-title").textContent=auxiliaryMode?"보조 항목":"질문 · 시간순";
    byId("mind-topics").closest(".mind-column").dataset.stageLabel=auxiliaryMode?"02 / AUXILIARY":"02 / TOPICS";
    byId("mind-questions").closest(".mind-column").dataset.stageLabel=auxiliaryMode?"03 / NOTES":"03 / QUESTIONS";
    const branch=QuestionAnswers.mindBranch(group,mindTopic,mindQuestion,mindTopicGroup);
    const questionIndex=branch.questions.findIndex(entry=>QuestionAnswers.occurrenceKey(entry)===mindQuestion);
    byId("question-stepper").hidden=mindSessionListOpen || auxiliaryMode;
    byId("question-previous").disabled=questionIndex<=0;
    byId("question-next").disabled=auxiliaryMode || !branch.questions.length || questionIndex>=branch.questions.length-1;
    byId("question-position").textContent=questionIndex<0?`질문 ${branch.questions.length}개`:`${questionIndex+1} / ${branch.questions.length}${branch.question?.anchor?"":" · 순서 미확정"}`;
    byId("mind-stage-nav").classList.toggle("auxiliary",auxiliaryMode);
    const stepLabels=["세션","주제",auxiliaryMode?"보조 항목":"질문","답변"];
    const enabled=[true,Boolean(session),mindTopic!==null,Boolean(branch.question)&&!auxiliaryMode];
    const current=mindSessionListOpen?0:branch.question&&!auxiliaryMode?3:mindTopic!==null?2:session?1:0;
    for(let step=0;step<4;step++) {
      const control=byId("mind-step-"+step);control.disabled=!enabled[step];control.hidden=step===3&&auxiliaryMode;
      control.setAttribute("aria-current",step===current?"step":"false");control.setAttribute("aria-label",stepLabels[step]+" 영역으로 이동");
      control.replaceChildren(el("span","0"+(step+1),"stage-no"),el("span",stepLabels[step]));
    }
    byId("mind-question-title-action").hidden=auxiliaryMode || !branch.question;
    if(!qaLoading && !qaFailedSessions.has(mindSession) && mindTopic!==null && !branch.topics.some(topic=>topic.value===mindTopic)) {
      mindTopic=null;mindQuestion="";mindPages[2]=mindPages[3]=0;
    }
    if(!qaLoading && !qaFailedSessions.has(mindSession) && mindQuestion && !branch.question) mindQuestion="";
    renderTopicEditor("mind",group,mindTopic);
    const emptySession=!catalogReady ? "세션을 불러오고 있습니다…" : "조건에 맞는 세션이 없습니다.";
    const noQuestions=qaFailedSessions.has(mindSession) ? "이 세션의 분석을 불러오지 못했습니다. 새로고침해 다시 확인해 주세요." : session?.hasAnalysis===false ? "아직 분석하지 않은 세션입니다. 아래 버튼으로 원문을 읽을 수 있습니다." : "저장된 질문이 없습니다. 원문 대화를 확인할 수 있습니다.";
    mindPage(byId("mind-sessions"),catalog,0,item=>mindSessionCard(item,item.id===mindSession,
      ()=>{const changed=item.id!==mindSession;if(changed){mindSession=item.id;mindTopic=null;mindQuestion="";mindTopicGroup="primary";mindPages=[mindPages[0],0,0,0];}mindSessionListOpen=false;close();rememberMind();renderMind(changed?1:null);byId("mind-session-picker").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});}),emptySession);
    if(!catalogOnly && session?.hasAnalysis!==false) {
    mindPage(byId("mind-topics"),branch.topics,1,topic=>mindCard(topic.title,auxiliaryMode?`보조 정보 · 항목 ${QuestionAnswers.itemsForTopic(data,group,topic.value).length}개`:topicOrderLabel(topic),topic.value===mindTopic,
      ()=>{const reveal=topic.value!==mindTopic?2:null;mindTopic=topic.value;mindQuestion="";mindPages[2]=mindPages[3]=0;close();rememberMind();renderMind(reveal);},"topic",topic.value),session?noQuestions:"왼쪽에서 세션을 선택하세요.");
    if(auxiliaryMode) mindPage(byId("mind-questions"),group?QuestionAnswers.itemsForTopic(data,group,mindTopic):[],2,item=>{
      const node=nodes.get(item.nodeId), card=mindCard(nodeTitle(node,item.source),`${label(node)} · 클릭하여 근거·관계 보기`,selected===node.id,
        ()=>choose(node.id,item.source),"auxiliary-item",JSON.stringify([item.source.session_id,item.source.source_node,node.id]));
      card.dataset.nodeId=node.id;card.dataset.sourceNode=item.source.source_node;return card;
    },mindTopic===null?"보조 정보를 선택하면 기존 항목이 펼쳐집니다.":"저장된 보조 항목이 없습니다.");
    else mindPage(byId("mind-questions"),branch.questions,2,entry=>{
      const key=QuestionAnswers.occurrenceKey(entry),node=nodes.get(entry.nodeId);
      const card=mindCard(nodeTitle(node,entry.source),`${entry.anchor?"원문 #"+(entry.anchor.index+1):"순서 미확정"} · 답변 연결 ${entry.answers.length}개${entry.answerEvidence.length?" · 답변 근거 "+entry.answerEvidence.length+"개":""}`,key===mindQuestion,
        ()=>selectMindQuestion(entry),"question",key);
      card.dataset.nodeId=entry.nodeId;card.dataset.sourceNode=entry.source.source_node;return card;
    },mindTopic===null?"주제를 선택하면 질문이 시간순으로 펼쳐집니다.":"이 주제에 저장된 질문이 없습니다.");
    const answerCards=auxiliaryMode?[]:[...branch.answers.map(answer=>({kind:"linked",answer})),...branch.answerEvidence.map(evidence=>({kind:"evidence",evidence}))];
    mindPage(byId("mind-answers"),answerCards,3,item=>{
      if(item.kind==="evidence") return answerEvidenceCard(branch.question,item.evidence,true);
      const answer=item.answer;
      const node=nodes.get(answer.nodeId),source=answer.source || resolveSource(node),card=mindCard(nodeTitle(node,source),"답변 · 선택하면 원문을 함께 읽습니다",selected===answer.nodeId,()=>{
        if(answer.anchor && liveCatalog){selected=answer.nodeId;selectedSource=source;close();renderMind();explorer.openReader(source.session_id,answer.anchor.messageId);}
        else choose(answer.nodeId,source);
      },"answer",answer.nodeId);
      card.dataset.nodeId=answer.nodeId;card.dataset.edgeIds=answer.edgeIds.join(" ");
      const wrapper=el("div","","answer-title-entry");wrapper.append(card,managementMenu("답변 관리",button("답변 표시 제목 수정",()=>openItemTitleEditor(itemTitleTarget(node,source,"답변")),"item-title-trigger")));return wrapper;
    },branch.question?"별도 답변 관계와 확인된 답변 근거가 없습니다. 원문 대화에서 확인하세요.":"질문을 선택하면 연결된 답변과 저장된 답변 근거가 펼쳐집니다.",
      item=>item.kind==="linked"?"연결된 분석 답변":"질문에 저장된 답변 근거");
    }
    const trail=byId("mind-selection");trail.replaceChildren();
    if(session) {
      const name=el("strong",sessionTitle(session));name.title=sessionTitle(session);
      trail.append(el("span","세션","breadcrumb-label"),name);
      if(mindTopic!==null) {
        const title=branch.topics.find(topic=>topic.value===mindTopic)?.title || "주제 미확정",topic=el("strong",title);topic.title=title;
        trail.append(el("span","/","breadcrumb-arrow"),el("span",auxiliaryMode?"보조 정보":"주제","breadcrumb-label"),topic);
      }
      if(branch.question){const name=el("strong",nodeTitle(nodes.get(branch.question.nodeId),branch.question.source));name.title=name.textContent;trail.append(el("span","/","breadcrumb-arrow"),el("span","질문","breadcrumb-label"),name);}
      const notice=assignmentNotice(session);if(notice)trail.append(el("span",notice,"mind-selection-note"));
    } else trail.append(el("span","먼저 세션을 선택하세요. 최근 대화순으로 표시합니다.","hint"));
    if(!catalogOnly) {
    const actions=byId("mind-actions");actions.replaceChildren();
    if(auxiliaryMode && mindTopic!==null) {
      actions.append(el("p","보조 항목을 누르면 기존 내용과 원문 근거, 저장된 관계를 확인할 수 있습니다.","hint"));
    } else if(branch.question) {
      const entry=branch.question,node=nodes.get(entry.nodeId);actions.append(el("h2",nodeTitle(node,entry.source)),el("p",node.summary || "요약 없음"));
      const source=button("이 질문의 앞뒤 원문 읽기",()=>{rememberMind();openSource(entry.source,source,entry.anchor.sourceMessageId);},"source-open");
      source.disabled=!entry.anchor || !/^https?:$/.test(location.protocol);
      actions.append(source,button("질문·근거 보기",()=>choose(entry.nodeId,entry.source)));
      if(entry.anchor)actions.append(button("하단 원문 패널",()=>explorer.openReader(entry.source.session_id,entry.anchor.messageId)));
    } else if(session) {
      const evidence=branch.topics.find(topic=>topic.value===mindTopic)?.userEvidence;
      if(evidence) {
        actions.append(el("p",`확인된 질문 위치가 없어, 같은 주제의 검증된 사용자 근거 원문 #${evidence.index+1}로 주제 순서를 보완했습니다.`,"hint"));
        const original=button("이 주제의 사용자 근거 원문 읽기",()=>openSource({session_id:mindSession,source_node:evidence.sourceNodeId},original,evidence.sourceMessageId),"topic-source-open");
        original.disabled=!/^https?:$/.test(location.protocol);actions.append(original);
      }
      const source=button("이 세션의 원문 읽기",()=>openSession(mindSession));source.disabled=!/^https?:$/.test(location.protocol);actions.append(source);
      if(liveCatalog)actions.append(button("하단 패널에서 원문 읽기",()=>explorer.openReader(mindSession)));
    }
    }
    byId("info").textContent=`${{primary:"일반 세션",subagent:"보조 작업",unknown:"분류 미확정"}[catalogKind]} ${catalog.length}개`;
    const notice=byId("mind-session-notice");notice.hidden=!session || session.hasAnalysis!==false || mindSessionListOpen;notice.replaceChildren();
    if(!notice.hidden)notice.append(el("span","분석 대기 · 아직 주제와 질문으로 정리되지 않았지만 저장된 원문은 읽을 수 있습니다."),button("원문 읽기",()=>explorer.openReader(session.id)));
    byId("view-info").textContent=qaLoading?"저장된 분석과 원문 근거를 확인하고 있습니다…":(qaFailed?"일부 세션의 분석·원문 순서를 불러오지 못했습니다. 새로고침해 다시 확인해 주세요. ":"")+"실선은 세션·주제 소속과 저장된 답변 관계, 점선은 질문에 저장된 답변 근거를 나타냅니다. 원문 순서가 없는 질문은 순서 미확정으로 표시합니다.";
    rawTurns.render(!mindSessionListOpen?session:null);
    if(focus) {
      const host=byId(focus.host);
      const target=focus.kind ? [...(host?.querySelectorAll(".mind-node") || [])].find(node=>node.dataset.kind===focus.kind&&node.dataset.key===focus.key)
        : [...(host?.querySelectorAll("button") || [])].find(node=>node.getAttribute("aria-label")===focus.label&&!node.disabled) || host?.querySelector(".mind-node");
      target?.focus({preventScroll:true});
    }
    scheduleMindDraw(animateColumn);
    if(catalogReady && !qaLoading && !qaFailedSessions.has(mindSession)) {
      if(mindRestorePending && mindSession) {
        mindRestorePending=false;
        const step=mindSessionListOpen?0:mindQuestion?2:mindTopic!==null?1:0;
        byId(mindHosts[step]).closest(".mind-column").scrollIntoView({block:"start",behavior:"auto"});
      } else if(Number.isInteger(animateColumn) && window.matchMedia("(min-width: 1200px)").matches) {
        byId("mind-canvas").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});
      }
      if(!mindSession)mindRestorePending=false;
    }
  }
  function reducedMotion() {return mindMotionQuery.matches || mindMotionControl.checked;}
  function centerMindSelection() {
    const open=document.body.classList.contains('has-source-reader'),revealed=open&&!sourcePanelWasOpen;sourcePanelWasOpen=open;
    if(mode!=="mind" || mindSessionListOpen)return;
    requestAnimationFrame(()=>{if(revealed){const target=window.matchMedia('(min-width: 1200px)').matches?byId('mind-canvas'):byId('mind-questions').closest('.mind-column');target.scrollIntoView({block:'start',behavior:'auto'});}
      for(const host of mindHosts){const list=byId(host).querySelector('.mind-node-list'),active=list?.querySelector('.mind-node[aria-pressed="true"]');
      if(active)list.scrollTop=Math.max(0,active.offsetTop-Math.max(0,(list.clientHeight-active.offsetHeight)/2));}scheduleMindDraw();});
  }
  function selectMindQuestion(entry) {
    const group=qaGroups.find(g=>g.sessionId===mindSession),branch=QuestionAnswers.mindBranch(group,mindTopic,mindQuestion,mindTopicGroup);
    const index=branch.questions.findIndex(q=>QuestionAnswers.occurrenceKey(q)===QuestionAnswers.occurrenceKey(entry));if(index<0)return;
    mindQuestion=QuestionAnswers.occurrenceKey(entry);mindPages[2]=Math.floor(index/8);mindPages[3]=0;
    selected=null;close();rememberMind();renderMind(3);
    if(entry.anchor)explorer.openReader(entry.source.session_id,entry.anchor.messageId);
    else explorer.unavailable("이 질문의 원문 순서가 미확정입니다. 질문·근거 보기에서 저장된 인용을 확인하세요.");
  }
  function searchTargets(hit) {
    return SessionAtlasTools.matchingEntries(qaGroups,hit).map(entry=>{
      const group=qaGroups.find(g=>g.sessionId===hit.sessionId),topic=group.topics.find(t=>t.id===entry.topicId);
      return {sessionId:hit.sessionId,questionKey:QuestionAnswers.occurrenceKey(entry),
        label:(topic?.title || "주제 미확정")+" › "+nodeTitle(nodes.get(entry.nodeId),entry.source)};
    });
  }
  function navigateExplorer(target) {
    const session=catalogRows().find(s=>s.id===target.sessionId);if(!session)return;
    const changed=mindSession!==session.id;mindSession=session.id;mode="mind";mindSessionListOpen=false;
    catalogKind=sessionKind(session);catalogParent=catalogKind==="subagent"?parentKey(session):"";
    byId("query").value="";byId("project").value="";restoredMindFilters=null;
    if(changed){mindTopic=null;mindQuestion="";mindTopicGroup="primary";mindPages=[0,0,0,0];}
    if(target.sourceMessage && session.hasAnalysis===false)rawTurns.target(target.sourceMessage);
    const group=qaGroups.find(g=>g.sessionId===session.id),entry=group?.entries.find(e=>QuestionAnswers.occurrenceKey(e)===target.questionKey);
    if(entry){const topic=group.topics.find(t=>t.id===entry.topicId);mindTopicGroup=topic?.displayGroup==="auxiliary"?"auxiliary":"primary";
      mindTopic=entry.topicId?"topic:"+entry.topicId:"unassigned";mindQuestion=QuestionAnswers.occurrenceKey(entry);
      const branch=QuestionAnswers.mindBranch(group,mindTopic,mindQuestion,mindTopicGroup);
      mindPages[1]=Math.max(0,Math.floor(branch.topics.findIndex(t=>t.value===mindTopic)/8));
      mindPages[2]=Math.max(0,Math.floor(branch.questions.findIndex(e=>QuestionAnswers.occurrenceKey(e)===mindQuestion)/8));mindPages[3]=0;
      selected=entry.nodeId;selectedSource=entry.source;
    }
    mindPages[0]=Math.max(0,Math.floor(scopedCatalog().slice().sort(recentSessions).findIndex(s=>s.id===session.id)/8));
    close();rememberMind();render();centerMindSelection();byId("mind-canvas").scrollIntoView({block:"start",behavior:"auto"});
  }
  function renderQuestionAnswers() {
    const host=byId("qa-timeline"), fragment=document.createDocumentFragment();
    const selection=sessionPicker();
    if(qaLoading) {
      host.replaceChildren(el("p","검증된 원문으로 질문 순서를 확인하고 있습니다…","empty"));
      byId("view-info").textContent="모델 호출 없이 저장된 분석과 원문 근거를 대조합니다.";
      return;
    }
    const term=byId("query").value.trim().toLocaleLowerCase(), project=byId("project").value;
    if(selection.auxiliarySelected) {
      const topic=selection.group.topics.find(topic=>"topic:"+topic.id===qaTopic);
      const items=QuestionAnswers.itemsForTopic(data,selection.group,qaTopic).filter(item=>{
        const node=nodes.get(item.nodeId);return !term || [nodeTitle(node,item.source),node.title,node.summary].join(" ").toLocaleLowerCase().includes(term);
      });
      host.replaceChildren(el("h2",`보조 정보 · ${topic?.title || "저장된 항목"}`));
      for(const item of items) {
        const node=nodes.get(item.nodeId), control=button(nodeTitle(node,item.source),()=>choose(node.id,item.source),"auxiliary-item");
        control.dataset.nodeId=node.id;control.dataset.sourceNode=item.source.source_node;host.append(control);
      }
      byId("view-info").textContent=`보조 항목 ${items.length}개 · 항목을 누르면 기존 근거와 관계를 볼 수 있습니다.`;
      return;
    }
    let total=0, confirmed=0, answered=0, withAnswerEvidence=0;
    for(const group of [selection.group]) {
      const entries=group.entries.filter(entry=>{
        const question=nodes.get(entry.nodeId), members=[question,...entry.answers.map(answer=>nodes.get(answer.nodeId))];
        const scopedTitles=[nodeTitle(question,entry.source),group.itemLabels?.[entry.source.source_node]?.title,
          ...entry.answers.flatMap(answer=>[nodeTitle(nodes.get(answer.nodeId),answer.source || resolveSource(nodes.get(answer.nodeId))),
            group.itemLabels?.[answer.source?.source_node]?.title])];
        return (!project || question.project===project) &&
          (!term || scopedTitles.join(" ").toLocaleLowerCase().includes(term) || members.some(node=>[node.title,node.summary,node.project,label(node)].join(" ").toLocaleLowerCase().includes(term)) ||
            entry.answerEvidence.some(evidence=>[evidenceTitle(entry,evidence),`어시스턴트 원문 #${evidence.index+1}`].join(" ").toLocaleLowerCase().includes(term)));
      });
      for(const topic of QuestionAnswers.byTopic({...group,entries},qaTopic)) {
      const section=el("section","","qa-session");
      section.dataset.topicId=topic.id;
      section.append(el("h2",`주제 · ${topic.title}`),el("p","이 주제의 질문을 원문 메시지 순서로 표시합니다. 같은 메시지 안의 질문 순서는 미확정입니다.","hint"));
      for(const known of [true,false]) {
        const subset=topic.entries.filter(entry=>Boolean(entry.anchor)===known);
        if(!subset.length) continue;
        section.append(el("h3",known?"시간순 질문과 답변":"순서 미확정","qa-section-title"));
        const list=el("ol","","qa-list");
        for(const entry of subset) {
          const question=nodes.get(entry.nodeId), row=el("li","","qa-row");
          row.dataset.questionId=question.id;row.dataset.sessionId=group.sessionId;row.dataset.questionState=known?"confirmed":"unconfirmed";
          if(known) row.dataset.questionIndex=entry.anchor.index;
          total++;if(known)confirmed++;if(entry.answers.length)answered++;if(entry.answerEvidence.length)withAnswerEvidence++;
          let timing="순서 미확정";
          if(entry.anchor) {
            const date=new Date(entry.timestamp), timestamp=entry.timestamp && Number.isFinite(date.getTime())?qaTime.format(date):"시간 정보 없음";
            timing=`메시지 #${entry.anchor.index+1} · ${timestamp}${entry.sameMessage?" · 동일 메시지의 질문":""}`;
          }
          row.append(el("p",timing,"qa-time"));
          const live=/^https?:$/.test(location.protocol);
          const content=button("",()=>{
            try {sessionStorage.setItem("context-graph-qa-return",JSON.stringify({node:question.id,session:group.sessionId}));} catch (_) {}
            return live?openSource(entry.source,content,entry.anchor.sourceMessageId):choose(question.id);
          },"qa-open");
          content.disabled=!entry.anchor;
          content.append(el("span","질문","qa-label"),el("strong",nodeTitle(question,entry.source),"qa-title"));
          if(question.summary) content.append(el("span",question.summary,"qa-summary"));
          for(const answer of entry.answers) {
            const node=nodes.get(answer.nodeId), block=el("span","","qa-answer");
            block.dataset.answerId=node.id;block.dataset.edgeIds=answer.edgeIds.join(" ");
            block.append(el("span","답변","qa-label"),el("strong",nodeTitle(node,answer.source || resolveSource(node)),"qa-title"));
            if(node.summary) block.append(el("span",node.summary,"qa-summary"));
            content.append(block);
          }
          if(!entry.answers.length) content.append(el("span",entry.answerEvidence.length?`별도 답변 관계 없음 · 아래 저장된 답변 근거 ${entry.answerEvidence.length}개에서 확인하세요.`:"별도 답변 관계와 확인된 답변 근거가 없습니다. 원문 대화에서 확인하세요.","qa-missing"));
          content.append(el("span",entry.anchor?(live?"이 질문의 앞뒤 원문 읽기 →":"분석·근거 보기 · 원문 이어 읽기는 수집기 주소에서 사용"):"사용자 원문 위치를 확인할 수 없습니다.","qa-read"));
          row.append(content,button("분석·근거 보기",()=>choose(question.id,entry.source),"qa-details"));
          const editors=el("div","","qa-title-actions");
          editors.append(button("질문 표시 제목 수정",()=>openItemTitleEditor(itemTitleTarget(question,entry.source,"질문")),"item-title-trigger"));
          entry.answers.forEach((answer,index)=>editors.append(button(`답변 ${index+1} 표시 제목 수정`,()=>openItemTitleEditor(itemTitleTarget(nodes.get(answer.nodeId),answer.source,"답변")),"item-title-trigger")));
          row.append(editors);
          for(const evidence of entry.answerEvidence) row.append(answerEvidenceCard(entry,evidence));
          list.append(row);
        }
        section.append(list);
      }
      fragment.append(section);
      }
    }
    if(!total) fragment.append(el("p",selection.session?.hasAnalysis===false ? "이 세션은 아직 분석하지 않아 저장된 주제·질문·답변이 없습니다. 위의 원문 읽기로 기록을 확인할 수 있습니다." : "선택한 세션·주제에서 조건에 맞는 질문이 없습니다. 다른 주제를 선택하거나 검색어를 지워 주세요.","empty"));
    host.replaceChildren(fragment);
    if(qaReturn) {
      const row=[...host.querySelectorAll(".qa-row")].find(row=>row.dataset.questionId===qaReturn.node && row.dataset.sessionId===qaReturn.session);
      if(row) {row.classList.add("qa-returned");row.scrollIntoView({block:"center"});qaReturn=null;}
    }
    if(qaLoading) byId("view-info").textContent="검증된 사용자 근거로 질문 순서를 확인하고 있습니다…";
    else byId("view-info").textContent=`질문 ${total}개 · 순서 확인 ${confirmed}개 · 순서 미확정 ${total-confirmed}개 · 답변 연결 ${answered}개 · 답변 근거가 있는 질문 ${withAnswerEvidence}개. 연결된 분석 답변과 질문에 저장된 답변 근거를 구분해 표시합니다.`+(qaFailed?" 일부 원문 순서를 확인하지 못했습니다. 수집기 주소에서 새로고침해 주세요.":"");
    byId("info").textContent=`질문 ${total}개`;
  }
  function details() {
    const node=nodes.get(selected), panel=byId("inspector");
    if(!node) return;
    panel.replaceChildren(el("span",[node.project,label(node),ko.status[node.status]].filter(Boolean).join(" · "),"pill"),el("h3",nodeTitle(node,selectedSource)),el("p",node.summary || "요약 없음"));
    const editTarget=itemTitleTarget(node,selectedSource);
    if(editTarget)panel.append(button("표시 제목 수정",()=>openItemTitleEditor(editTarget),"item-title-trigger"));
    else panel.append(el("p","세션의 질문·답변 목록에서 항목을 선택하면 표시 제목을 수정할 수 있습니다.","hint"));
    const provenances=node.provenance || [];
    for(const provenance of provenances) {
      if(/^https?:$/.test(location.protocol) && provenance.source_node) {
        const control=button("원문 읽기",()=>openSource(provenance,control),"source-open");
        panel.append(control);
      }
    }
    panel.append(el("h2","관련 내용"));
    if(!adjacent.get(selected)?.length) panel.append(el("p","함께 연결된 내용이 없습니다.","hint"));
    for(const edge of adjacent.get(selected) || []) {
      const source=nodes.get(edge.from), target=nodes.get(edge.to);
      if(!source || !target) continue;
      const item=el("div","","relation");
      item.dataset.from=edge.from; item.dataset.to=edge.to; item.dataset.relation=edge.type;
      const peer=edge.from===selected?target:source, outgoing=edge.from===selected;
      const descriptions={
        answers:["이 항목이 답변하는 내용","이 항목에 답변하는 내용"],
        supports:["이 항목이 뒷받침하는 내용","이 항목을 뒷받침하는 내용"],
        contradicts:["이 항목과 상충하는 내용","이 항목과 상충하는 내용"],
        supersedes:["이 항목이 대체한 내용","이 항목을 대체한 내용"],
        implements:["이 항목이 구현하는 내용","이 항목을 구현한 내용"],
        depends_on:["이 항목에 필요한 내용","이 항목을 필요로 하는 내용"]
      };
      const description=descriptions[edge.type]?.[outgoing?0:1] || (edge.type==="related"?"함께 볼 내용":`${relation(edge)} · ${outgoing?"이 항목에서 연결":"이 항목으로 연결"}`);
      item.append(el("p",description,"relation-label"),button(nodeTitle(peer,resolveSource(peer)),()=>choose(peer.id)));
      panel.append(item);
    }
    panel.append(el("h2","원문 근거"),el("p","인용문과 기록 정보는 펼쳐서 확인할 수 있습니다.","hint"));
    if(!provenances.length) panel.append(el("p","저장된 인용 근거가 없습니다.","hint"));
    provenances.forEach((provenance,index)=>{
      const section=el("details");
      section.append(el("summary",`근거가 있는 대화 ${index+1} · 인용 ${provenance.evidence?.length || 0}개`));
      for(const evidence of provenance.evidence || []) section.append(el("blockquote",evidence.quote || "(인용문 없음)","quote"),el("small",`${evidence.verified?"저장된 검증 근거":"미검증"} · ${evidence.messageId}`));
      const metadata=el("details");
      metadata.append(el("summary","기록 정보"),el("p",`세션 ${provenance.session_id}${provenance.branch?" · "+provenance.branch:""}`));
      section.append(metadata);panel.append(section);
    });
  }
  const NS="http://www.w3.org/2000/svg";
  function svgEl(tag,attributes={},text="") {
    const node=document.createElementNS(NS,tag);
    for(const [key,value] of Object.entries(attributes)) node.setAttribute(key,String(value));
    node.textContent=text; return node;
  }
  function draw() {
    const host=byId("diagram"); host.replaceChildren();
    if(!selected) return;
    const edges=adjacent.get(selected) || [];
    const peers=[...new Set(edges.map(edge=>edge.from===selected?edge.to:edge.from))].filter(id=>id!==selected && nodes.has(id));
    neighborPage=Math.min(neighborPage,Math.max(0,Math.ceil(peers.length/neighborSize)-1));
    const visible=peers.slice(neighborPage*neighborSize,(neighborPage+1)*neighborSize);
    const incoming=visible.filter(id=>edges.some(edge=>edge.from===id && edge.to===selected));
    const outgoing=visible.filter(id=>!incoming.includes(id));
    const width=980, height=Math.max(340,Math.max(incoming.length,outgoing.length)*135+60), nw=220, nh=108;
    const positions=new Map([[selected,{x:(width-nw)/2,y:(height-nh)/2}]]);
    incoming.forEach((id,i)=>positions.set(id,{x:16,y:30+i*135}));
    outgoing.forEach((id,i)=>positions.set(id,{x:width-nw-16,y:30+i*135}));
    const wrapper=el("div","","diagram-viewport");
    const svg=svgEl("svg",{viewBox:`0 0 ${width} ${height}`,width,height,role:"group","aria-label":"선택 항목과 직접 연결된 관계"});
    if(fit) { svg.style.width="100%"; svg.style.height="auto"; }
    const defs=svgEl("defs"), marker=svgEl("marker",{id:"graph-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:7,markerHeight:7,orient:"auto-start-reverse"});
    marker.append(svgEl("path",{d:"M 0 0 L 10 5 L 0 10 z",class:"arrow-head"})); defs.append(marker); svg.append(defs);
    let edgeIndex=0;
    for(const edge of edges) {
      if(!positions.has(edge.from) || !positions.has(edge.to) || edge.from===edge.to) continue;
      const from=positions.get(edge.from), to=positions.get(edge.to), forward=from.x<to.x;
      const x1=from.x+(forward?nw:0), x2=to.x+(forward?0:nw), y1=from.y+nh/2,y2=to.y+nh/2;
      const mid=(x1+x2)/2, bend=(edgeIndex++%3-1)*12;
      svg.append(svgEl("path",{d:`M ${x1} ${y1} C ${mid} ${y1+bend}, ${mid} ${y2+bend}, ${x2} ${y2}`,class:"edge","marker-end":"url(#graph-arrow)","data-from":edge.from,"data-to":edge.to,"data-relation":edge.type}),
        svgEl("text",{x:mid,y:(y1+y2)/2-9+bend,"text-anchor":"middle",class:"edge-label"},relation(edge)));
    }
    for(const [id,position] of positions) {
      const node=nodes.get(id);
      const title=nodeTitle(node,id===selected?selectedSource:resolveSource(node));
      const g=svgEl("g",{class:"graph-node"+(id===selected?" active":""),transform:`translate(${position.x},${position.y})`,role:"button",tabindex:0,"aria-label":title,"data-node-id":id});
      g.append(svgEl("title",{},title),svgEl("rect",{width:nw,height:nh,rx:10,class:"node-box"}),svgEl("text",{x:12,y:23,class:"node-kind"},label(node)));
      const chars=Array.from(title), perLine=12;
      for(let line=0;line<3 && line*perLine<chars.length;line++) g.append(svgEl("text",{x:12,y:48+line*22,class:"node-title"},chars.slice(line*perLine,(line+1)*perLine).join("")+(line===2 && chars.length>36?"…":"")));
      g.addEventListener("click",()=>choose(id));
      g.addEventListener("keydown",event=>{if(event.key==="Enter" || event.key===" "){event.preventDefault();choose(id);}});
      svg.append(g);
    }
    wrapper.append(svg);host.append(wrapper);
    if(!fit) wrapper.scrollLeft=Math.max(0,(width-wrapper.clientWidth)/2);
    byId("neighbors-info").textContent=peers.length?`연결 항목 ${neighborPage*neighborSize+1}–${Math.min((neighborPage+1)*neighborSize,peers.length)} / ${peers.length}`:"직접 연결된 항목이 없습니다";
    byId("neighbors-prev").disabled=neighborPage===0;
    byId("neighbors-next").disabled=(neighborPage+1)*neighborSize>=peers.length;
  }
  function choose(id,source=null) {
    if(!nodes.has(id)) return;
    selectedSource=resolveSource(nodes.get(id),source);
    selected=id; neighborPage=0;
    if(mode==="qa") mode="overview";
    document.body.classList.add("has-detail");byId("detail-panel").hidden=false;
    history.replaceState(null,"","#"+new URLSearchParams({node:id}));
    details();byId("detail-panel").scrollTop=0;render();
  }
  function render() {
    byId('explorer-analyze').disabled=!liveCatalog || window.parent===window || !(mode==='mind'?mindSession:mode==='qa'?qaSession:'');
    if(byId("item-title-dialog").open)renderItemTitleEditor();
    document.body.classList.toggle("mind-mode",mode==="mind");
    byId("session-mindmap").hidden=mode!=="mind";
    syncSessionViewControls();
    byId("query").placeholder=mode==="mind"?"제목·첫 요청·프로젝트 검색":"제목·요약 검색";
    const matches=filtered(); page=Math.min(page,Math.max(0,Math.ceil(matches.length/size)-1));
    byId("info").textContent=`${matches.length} / ${data.nodes.length}개 항목`;
    byId("overview").hidden=mode!=="overview";byId("pages").hidden=mode!=="overview";byId("focus").hidden=mode!=="focus";
    byId("qa-timeline").hidden=mode!=="qa";
    byId("qa-browser").hidden=mode!=="qa";
    byId("catalog-scope").hidden=!["mind","qa"].includes(mode);
    byId("category").hidden=["qa","mind"].includes(mode);
    byId("qa-mode").setAttribute("aria-pressed",String(mode==="qa"));
    byId("overview-mode").setAttribute("aria-pressed",String(mode==="overview"));byId("focus-mode").setAttribute("aria-pressed",String(mode==="focus"));byId("focus-mode").disabled=!selected;
    const auxiliaryLabel=mode==="overview"?"전체 분석 항목":mode==="focus"?"관계 그림":"";
    byId("auxiliary-view-menu").dataset.active=String(Boolean(auxiliaryLabel));
    byId("auxiliary-view-label").textContent=auxiliaryLabel?"보조 보기 · "+auxiliaryLabel:"보조 보기";
    byId("auxiliary-view-hint").hidden=Boolean(selected);
    byId("view-info").textContent=mode==="focus"?"선택 항목의 전체 직접 연결 · 검색 필터와 별도":"궁금한 항목을 선택하세요. 내용을 읽은 뒤 원문 대화로 이동할 수 있습니다.";
    const area=byId("overview");area.replaceChildren();
    for(const node of matches.slice(page*size,(page+1)*size)) area.append(card(node));
    if(!matches.length) area.append(el("p","조건에 맞는 분석 항목이 없습니다.","empty"));
    byId("page-info").textContent=`${matches.length?page*size+1:0}–${Math.min((page+1)*size,matches.length)} / ${matches.length}`;
    byId("previous").disabled=page===0;byId("next").disabled=(page+1)*size>=matches.length;
    if(mode==="focus") draw();
    if(mode==="qa") renderQuestionAnswers();
    if(mode==="mind") renderMind();
    else {rawTurns.render(null);explorer.closeReader();explorer.updateSession(mode==="qa"?qaSession:"");}
    explorer.refresh();
  }
  if(mode==="mind" && restoredMindFilters) byId("query").value=restoredMindFilters.query;
  else restoredMindFilters=null;
  updateProjectOptions();
  for(const value of [...new Set(data.nodes.map(node=>node.classification))].sort()){const option=el("option",ko.classification[value]||value);option.value=value;byId("category").append(option);}
  byId("totals").textContent=`${data.nodes.length}개 항목 · ${data.edges.length}개 관계 · 분석된 세션 ${data.sessionCount}개`;
  for(const [id,event] of [["query","input"],["project","change"],["category","change"]])byId(id).addEventListener(event,()=>{page=0;if(mode==="focus")mode="overview";if(mode==="mind") {restoredMindFilters=null;mindPages[0]=0;}render();if(mode==="mind")rememberMind();});
  byId("reset").addEventListener("click",()=>{for(const id of ["query","project","category"])byId(id).value="";page=0;restoredMindFilters=null;if(mode==="mind")mindPages[0]=0;render();if(mode==="mind")rememberMind();});
  byId("qa-session").addEventListener("change",()=>{qaSession=byId("qa-session").value;qaTopic="";qaReturn=null;saveQuestionFilter();render();});
  for(const id of ["catalog-kind","catalog-parent"]) byId(id).addEventListener("change",()=>{
    if(id==="catalog-kind") catalogKind=byId(id).value;
    else catalogParent=byId(id).value;
    mindSession="";mindTopic=null;mindQuestion="";mindTopicGroup="primary";mindPages=[0,0,0,0];qaSession="";qaTopic="";qaReturn=null;
    render();rememberScope();rememberMind();saveQuestionFilter();
  });
  byId("qa-topic").addEventListener("change",()=>{qaTopic=byId("qa-topic").value;qaReturn=null;saveQuestionFilter();render();});
  for(let step=0;step<4;step++)byId("mind-step-"+step).addEventListener("click",()=>{
    if(step>0 && mindSessionListOpen){mindSessionListOpen=false;renderMind();rememberMind();}
    const host=byId(mindHosts[step]);host.closest(".mind-column").scrollIntoView({block:"start",behavior:mindMotionQuery.matches||mindMotionControl.checked?"auto":"smooth"});
    host.focus({preventScroll:true});
  });
  byId("mind-session-change").addEventListener("click",()=>{
    showSessionView(!mindSessionListOpen);
  });
  byId("mind-question-title-edit").addEventListener("click",()=>{
    const group=qaGroups.find(group=>group.sessionId===mindSession),entry=group?.entries.find(entry=>QuestionAnswers.occurrenceKey(entry)===mindQuestion);
    if(entry)openItemTitleEditor(itemTitleTarget(nodes.get(entry.nodeId),entry.source,"질문"));
  });
  for(const [id,direction] of [["question-previous",-1],["question-next",1]])byId(id).addEventListener("click",()=>{
    if(rawTurns.active()){rawTurns.move(direction);return;}
    const group=qaGroups.find(g=>g.sessionId===mindSession),branch=QuestionAnswers.mindBranch(group,mindTopic,mindQuestion,mindTopicGroup);
    const index=branch.questions.findIndex(e=>QuestionAnswers.occurrenceKey(e)===mindQuestion),next=branch.questions[index+direction];if(next)selectMindQuestion(next);
  });
  byId("item-title-save").addEventListener("click",()=>saveItemTitle());
  byId("item-title-reset").addEventListener("click",()=>saveItemTitle(true));
  byId("item-title-close").addEventListener("click",()=>byId("item-title-dialog").close());
  byId("item-title-input").addEventListener("input",()=>{
    if(itemDisplayTarget)itemTitleDrafts.set(itemTargetKey(itemDisplayTarget),byId("item-title-input").value);
    itemDisplayResult=null;byId("item-title-status").textContent="";
  });
  byId("item-title-input").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();saveItemTitle();}});
  for(const prefix of ["mind","qa"]) {
    byId(prefix+"-session-name-save").addEventListener("click",()=>editSessionName(prefix));
    byId(prefix+"-session-name-reset").addEventListener("click",()=>editSessionName(prefix,true));
    byId(prefix+"-session-name").addEventListener("input",()=>{
      const input=byId(prefix+"-session-name");if(input.dataset.sessionKey)sessionNameDrafts.set(input.dataset.sessionKey,input.value);
      sessionNameResult=null;byId(prefix+"-session-name-status").textContent="";
    });
    byId(prefix+"-session-name").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();editSessionName(prefix);}});
    byId(prefix+"-session-name-details").addEventListener("toggle",()=>scheduleMindDraw());
    byId(prefix+"-topic-edit").addEventListener("click",()=>editTopicDisplay(prefix));
    byId(prefix+"-topic-name-save").addEventListener("click",()=>editTopicDisplay(prefix,"name"));
    byId(prefix+"-topic-name-reset").addEventListener("click",()=>editTopicDisplay(prefix,"reset"));
    byId(prefix+"-topic-name").addEventListener("input",()=>{
      const input=byId(prefix+"-topic-name");if(input.dataset.topicKey)topicNameDrafts.set(input.dataset.topicKey,input.value);
      topicDisplayResult=null;byId(prefix+"-topic-edit-status").textContent="";
    });
    byId(prefix+"-topic-name").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();editTopicDisplay(prefix,"name");}});
    byId(prefix+"-topic-name-details").addEventListener("toggle",()=>scheduleMindDraw());
  }
  for(const [id,scope] of [["mind-primary-topics","primary"],["mind-auxiliary-topics","auxiliary"]]) byId(id).addEventListener("click",()=>{
    if(mindTopicGroup===scope)return;
    mindTopicGroup=scope;mindTopic=null;mindQuestion="";mindPages[1]=mindPages[2]=mindPages[3]=0;
    close();rememberMind();renderMind(1);
  });
  byId("qa-session-raw").addEventListener("click",()=>openSession());
  function syncSessionViewControls() {
    byId("session-list-mode").setAttribute("aria-pressed",String(mode==="mind" && mindSessionListOpen));
    byId("mind-mode").setAttribute("aria-pressed",String(mode==="mind" && !mindSessionListOpen));
  }
  function showSessionView(list) {
    if(mode!=="mind")byId("query").value="";
    mode="mind";mindSessionListOpen=list;
    history.replaceState(null,"",location.pathname+location.search);close();rememberMind();render();
    byId("mind-canvas").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});
  }
  byId("session-list-mode").addEventListener("click",()=>showSessionView(true));
  byId("mind-mode").addEventListener("click",()=>showSessionView(false));
  byId("qa-mode").addEventListener("click",()=>{mode="qa";byId("query").value="";try {sessionStorage.setItem("context-graph-reading-mode","qa");} catch (_) {} history.replaceState(null,"",location.pathname+location.search);close();render();});
  window.addEventListener("resize",()=>scheduleMindDraw());
  window.addEventListener("pagehide",()=>{
    if(mode==="mind") rememberMind();
    catalogStopped=true;clearTimeout(catalogTimer);catalogRequest?.abort();
  });
  window.addEventListener("pageshow",()=>{if(catalogStopped) {catalogStopped=false;refreshCatalog();}});
  document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible") refreshCatalog();});
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin || event.source!==window.parent || event.data?.type!=='context-graph-refresh-display')return;
    refreshCatalog();loadQuestionOrder();
  });
  mindMotionQuery.addEventListener("change",syncMindMotion);
  mindMotionControl.addEventListener("change",()=>{try {sessionStorage.setItem("context-graph-reduced-motion",String(mindMotionControl.checked));} catch (_) {} syncMindMotion();});
  // Transient DOM-only lifecycle markers let motion be checked without exposing
  // titles or evidence, and never become part of the saved graph.
  for(const type of ["animationstart","animationend"]) byId("mind-canvas").addEventListener(type,event=>{
    if(!["mind-branch-draw","mind-node-unfold"].includes(event.animationName))return;
    if(type==="animationstart")event.target.dataset.motionStarted="true";
    event.target.dataset.motionState=type==="animationstart"?"running":"complete";
    if(type==="animationend")event.target.dataset.motionElapsed=String(event.elapsedTime);
  });
  byId("overview-mode").addEventListener("click",()=>{mode="overview";render();});
  byId("focus-mode").addEventListener("click",()=>{if(selected){mode="focus";render();}});
  for(const id of ["session-list-mode","mind-mode","qa-mode","overview-mode","focus-mode"]) byId(id).addEventListener("click",()=>{
    byId("auxiliary-view-menu").open=false;
    if(id==="overview-mode" || id==="focus-mode")byId("auxiliary-view-toggle").focus();
  });
  byId("auxiliary-view-menu").addEventListener("keydown",event=>{
    if(event.key==="Escape") {byId("auxiliary-view-menu").open=false;byId("auxiliary-view-toggle").focus();event.stopPropagation();}
  });
  byId("previous").addEventListener("click",()=>{page--;render();byId("overview").scrollIntoView({block:"start"});});
  byId("next").addEventListener("click",()=>{page++;render();byId("overview").scrollIntoView({block:"start"});});
  byId("neighbors-prev").addEventListener("click",()=>{neighborPage--;draw();});byId("neighbors-next").addEventListener("click",()=>{neighborPage++;draw();});
  byId("fit").addEventListener("click",()=>{fit=!fit;byId("fit").setAttribute("aria-pressed",String(fit));draw();});
  function close(){byId("detail-panel").hidden=true;document.body.classList.remove("has-detail");}
  byId("close-detail").addEventListener("click",close);document.addEventListener("keydown",event=>{if(event.key==="Escape")close();});
  let navigationStorage=sessionStorage;try{navigationStorage=window.localStorage || sessionStorage;}catch(_){}
  byId('explorer-analyze').addEventListener('click',()=>{
    const sid=mode==='mind'?mindSession:mode==='qa'?qaSession:'',session=catalogRows().find(s=>s.id===sid);
    if(!session || !liveCatalog || window.parent===window)return;
    window.parent.postMessage({type:'context-graph-analyze',session:sid,title:session.displayTitle||session.title},location.origin);
  });
  const explorer=SessionAtlasTools.create({document,fetch,storage:navigationStorage,live:liveCatalog,
    getSessions:catalogRows,getTargets:searchTargets,onNavigate:navigateExplorer,onLayout:centerMindSelection,
    onFullPage:(session,message)=>{rememberMind();if(window.parent!==window)window.parent.postMessage({type:message?"context-graph-open-source":"context-graph-open-session",session,...(message?{message}:{})},location.origin);
      else location.assign("./?"+new URLSearchParams({view:"timeline",session,...(message?{message}:{})}));}});
  const rawTurns=RawTurns.create({document,fetch,live:liveCatalog,page:mindPage,card:mindCard,layout:rawSessionLayout,
    openSource:(sid,message)=>explorer.openReader(sid,message),redraw:()=>{if(mode==='mind')renderMind();},remember:rememberMind,
    locate:index=>{mindPages[2]=Math.max(0,Math.floor(index/8));mindPages[3]=0;}});
  const requested=new URLSearchParams(location.hash.slice(1)).get("node");
  syncMindMotion();
  if(requested && nodes.has(requested)) choose(requested);else render();
  refreshCatalog();
  loadQuestionOrder();
})();
