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
  let selected = null;
  let selectedSource=null,itemDisplayTarget=null,itemDisplaySaving=false,itemDisplayResult=null;
  const itemTitleDrafts=new Map();
  const savedContext=JSON.parse(byId("qa-context").textContent);
  const liveCatalog=/^https?:$/.test(location.protocol);
  let qaGroups = QuestionAnswers.build(data,savedContext), qaLoading = true, qaFailed = 0;
  const qaFailedSessions=new Set();
  let qaCatalog=Object.entries(savedContext).map(([id,context])=>({id,...context.session}));
  let catalogReady=!liveCatalog,catalogTimer=0,catalogRequest=null,catalogStopped=false,catalogSignature="",catalogEpoch=0;
  let mindSession="",mindTopic=null,mindQuestion="",mindAnswer="",mindPage=0,mindTopicGroup="primary",mindRootKind="session";
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
  let mindDrawFrame=0,mindReveal=null,mindPlan=null,mindAnswerSelection=null;
  // The line layer is kept by reference: clearing the tree must not drop it from the document.
  const mindLinks=byId("mind-links");
  // Camera for the tree box: translate(x,y) scale(k). Fitted when a session opens, then moved by the reader.
  let mindCamera={x:0,y:0,k:1},mindCameraRoot="",mindTerm="",mindSearchHits=0;
  const treeHostEl=byId("mind-tree-host"),treeEl=byId("mind-tree");
  let sourcePanelWasOpen=false;
  // Sessions without analysis branch into request groups keyed "raw:<group>"; they are never saved topics.
  const isRawTopic=value=>typeof value==="string" && value.startsWith("raw:");
  const mindMotionQuery=window.matchMedia("(prefers-reduced-motion: reduce)");
  const mindMotionControl=byId("mind-reduce-motion");
  try {mindMotionControl.checked=sessionStorage.getItem("context-graph-reduced-motion")==="true";} catch (_) {}
  try {const state=JSON.parse(sessionStorage.getItem("context-graph-mind-selection") || "{}");mindSession=state.session || "";if(state.topicGroup==="auxiliary")mindTopicGroup="auxiliary";mindTopic=state.topic ?? null;mindQuestion=state.question || "";mindAnswer=typeof state.answer==="string"?state.answer:"";mindRootKind=state.rootKind==="project"?"project":"session";if(Number.isInteger(state.page) && state.page>=0)mindPage=state.page;else if(Array.isArray(state.pages) && Number.isInteger(state.pages[0]) && state.pages[0]>=0)mindPage=state.pages[0];if(state.filters && typeof state.filters.query==="string" && typeof state.filters.project==="string")restoredMindFilters=state.filters;} catch (_) {}
  // A restored project root shows its tree straight away; otherwise the list stays up until a session is chosen.
  let mindSessionListOpen=!mindSession && !(mindRootKind==="project" && restoredMindFilters?.project);
  try {const state=JSON.parse(sessionStorage.getItem("context-graph-mind-selection") || "{}");if(typeof state.sessionListOpen==="boolean")mindSessionListOpen=state.sessionListOpen;} catch (_) {}
  const qaTime = new Intl.DateTimeFormat("ko-KR",{dateStyle:"short",timeStyle:"short"});
  const label = node => ko.classification[node.classification] || node.classification;
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
    const sid=mindSession;
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
      rememberMind();
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
    const sid=mindSession,session=catalogRows().find(value=>value.id===sid);
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
      if(mindSession===sid) {
        const term=byId("query").value.trim().toLocaleLowerCase();
        if(term && !sessionSearchText(updated).includes(term)) {
          byId("query").value="";restoredMindFilters=null;
          const rows=scopedCatalog().slice().sort(recentSessions)
            .filter(value=>!byId("project").value || value.project===byId("project").value);
          mindPage=Math.max(0,Math.floor(rows.findIndex(value=>value.id===sid)/8));
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
    const session=mindSession,selection=mindTopic;
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
      if(!naming && mindSession===session && mindTopic===selection) {mindTopicGroup=target;mindQuestion="";mindAnswer="";close();rememberMind();}
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
  async function openSource(provenance,control,sourceMessageId="") {
    rememberMind();
    control.disabled=true;
    try {
      const query=new URLSearchParams({session:provenance.session_id,limit:"1"});
      const response=await fetch("timeline/messages.json?"+query,{cache:"no-store"});
      if(!response.ok) throw new Error("Unavailable");
      const view=await response.json();
      const anchors=view.analysis.anchors[provenance.source_node] || [];
      const anchor=sourceMessageId ? anchors.find(item=>item.sourceMessageId===sourceMessageId) : anchors[0];
      if(!anchor) { control.textContent="원문 연결 시점 미확정"; return; }
      explorer.openReader(provenance.session_id,anchor.messageId);
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
        renderMind(null,!initial);
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
  function openSession(session=mindSession) {
    if(!session) return;
    rememberMind();
    explorer.readSession(session);
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
    try {sessionStorage.setItem("context-graph-mind-selection",JSON.stringify({session:mindSession,topic:mindTopic,question:mindQuestion,...(mindAnswer?{answer:mindAnswer}:{}),page:mindPage,...(mindSessionListOpen || !mindSession?{sessionListOpen:mindSessionListOpen}:{}),...(mindTopicGroup==="auxiliary"?{topicGroup:"auxiliary"}:{}),...(mindRootKind==="project"?{rootKind:"project"}:{}),filters:{query:byId("query").value,project:byId("project").value}}));} catch (_) {}
  }
  function mindSessionCard(session,active,action) {
    const title=sessionTitle(session);
    const card=button("",action,"mind-node mind-session-node");card.setAttribute("aria-pressed",String(active));
    card.dataset.kind="session";card.dataset.key=session.id;
    const heading=el("span","","session-heading");
    heading.append(el("strong",title,"name"));
    if(session.displayTitle) heading.append(el("span","표시 이름","session-title-source"));
    else if(session.titleSource==="first_user") heading.append(el("span","첫 요청","session-title-source"));
    else if(session.titleSource==="own_request") heading.append(el("span","분기 후 요청","session-title-source"));
    const stamp=activityValue(session);
    const activity=Number.isFinite(stamp) ? `최근 ${qaTime.format(new Date(stamp))}` : "활동 시각 미확정";
    card.title=`${title}\n세션 ID: ${session.id}`;
    const identity=el("span","","session-identity");
    identity.append(heading,
      el("span",`${session.provider==='claude'?'Claude Code':'Codex'} · ${session.project || "프로젝트 미확정"}`,"session-project"),
      el("span",activity,"mind-activity"),
      el("span",`${session.hasAnalysis?"분석 있음":"분석 대기"}${session.messageCount!==undefined?" · 메시지 "+session.messageCount+"개":""}`,"mind-meta"));
    if(catalogRows().filter(item=>sessionTitle(item)===title).length>1)identity.append(el("span",`같은 제목 구분 · ${session.id.slice(-8)}`,"mind-activity session-key"));
    const helpers=catalogRows().filter(item=>sessionKind(item)==="subagent" && parentKey(item)===session.id).length;
    if(sessionKind(session)==="primary" && helpers) identity.append(el("span",`보조 작업 ${helpers}개` ,"mind-activity"));
    card.append(identity);
    const previews=session.requestPreviews;
    for(const [key,caption] of [["first",previews?.scope==="own"?"분기 후 첫 요청":"첫 요청"],["latest","최근 요청"]]) {
      const entry=previews?.[key],box=el("span","","session-request-preview");box.dataset.preview=key;
      const missing=previews?.scope==="unconfirmed"?"이 세션의 고유 요청을 확인할 수 없습니다.":previews?"확인할 요청이 없습니다.":"실시간 뷰어에서 원문 미리보기를 확인할 수 있습니다.";
      box.append(el("span",caption,"session-preview-label"),el("span",entry?.text || missing,"session-preview-text"));
      if(entry?.messageId)box.dataset.messageId=entry.messageId;
      card.append(box);
    }
    return card;
  }
  function sessionPage(catalog,empty) {
    const host=byId("mind-sessions"),pageSize=8,previousList=host.querySelector(".mind-node-list");
    const owner=JSON.stringify([catalogKind,catalogParent,byId("query").value,byId("project").value]);
    if(catalogReady) mindPage=Math.min(mindPage,Math.max(0,Math.ceil(catalog.length/pageSize)-1));
    const page=mindPage;
    const keepScroll=previousList?.dataset.owner===owner && previousList.dataset.page===String(page) && previousList.querySelector(".mind-node");
    const scrollTop=keepScroll?previousList.scrollTop:0;
    host.replaceChildren();const list=el("div","","mind-node-list");
    list.dataset.owner=owner;list.dataset.page=String(page);host.append(list);
    for(const item of catalog.slice(page*pageSize,(page+1)*pageSize)) list.append(mindSessionCard(item,item.id===mindSession,()=>chooseSession(item)));
    if(!catalog.length) list.append(el("p",empty,"mind-empty"));
    list.scrollTop=scrollTop;
    if(catalog.length>pageSize) {
      const paging=el("div","","paging"),previous=button("이전",()=>{mindPage--;renderMind();rememberMind();}),next=button("다음",()=>{mindPage++;renderMind();rememberMind();});
      previous.setAttribute("aria-label","이전 세션 노드");next.setAttribute("aria-label","다음 세션 노드");
      previous.disabled=page===0;next.disabled=(page+1)*pageSize>=catalog.length;
      paging.append(previous,el("span",`${page*pageSize+1}–${Math.min((page+1)*pageSize,catalog.length)} / ${catalog.length}`,"hint"),next);host.append(paging);
    }
  }
  function chooseSession(item) {
    const changed=item.id!==mindSession;
    if(changed){mindSession=item.id;mindTopic=null;mindQuestion="";mindAnswer="";mindTopicGroup="primary";}
    mindRootKind="session";mindSessionListOpen=false;close();rememberMind();renderMind(changed?item.id:null);
    byId("mind-canvas").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});
  }
  // Tree nodes are plain specs: {kind,key,label,number,title,expanded,selected,count,children,data,action}.
  const note=(label,key="note")=>({kind:"note",key,label});
  function treeElement(entry) {
    const spec=entry.node;
    if(spec.kind==="note") {
      const text=el("span",spec.label,"mind-note");text.title=spec.label;
      text.style.left=entry.x+"px";text.style.top=entry.y+"px";text.style.height=entry.height+"px";text.style.maxWidth=entry.width+"px";
      return text;
    }
    const node=button("",()=>spec.action?.(),`mind-node depth-${entry.depth} kind-${spec.kind}`);
    node.setAttribute("aria-pressed",String(Boolean(spec.selected)));
    const count=spec.count ?? (spec.children?.length || 0);
    if(count || spec.expanded)node.setAttribute("aria-expanded",String(Boolean(spec.expanded)));
    node.dataset.kind=spec.kind;node.dataset.key=spec.key;node.dataset.depth=String(entry.depth);
    if(entry.parent)node.dataset.parent=entry.parent.node.key;
    for(const [name,value] of Object.entries(spec.data || {}))if(value!==undefined && value!==null)node.dataset[name]=String(value);
    node.title=spec.title || spec.label;
    node.style.left=entry.x+"px";node.style.top=entry.y+"px";node.style.height=entry.height+"px";node.style.maxWidth=entry.width+"px";
    if(spec.number)node.append(el("span",spec.number,"tree-no"));
    node.append(el("strong",spec.label,"name"));
    if(count && !spec.expanded)node.append(el("span",String(count),"tree-count"));
    else if(spec.badge && !spec.expanded)node.append(el("span",spec.badge,"tree-count"));
    if(spec.enter)node.addEventListener("dblclick",event=>{event.preventDefault();spec.enter();});
    if(mindTerm) {
      // A branch lights up when its own label matches; a folded branch also reports matches hidden inside it.
      if(spec.kind!=="session" && String(spec.label).toLocaleLowerCase().includes(mindTerm)){node.classList.add("mind-hit");mindSearchHits++;}
      if(spec.hits && !spec.expanded){node.classList.add("mind-hit");node.append(el("span",`검색 ${spec.hits}`,"tree-hits"));mindSearchHits+=spec.hits;}
    }
    return node;
  }
  const countHits=labels=>mindTerm?labels.filter(label=>String(label || "").toLocaleLowerCase().includes(mindTerm)).length:0;
  const compactTree=()=>(byId("mind-canvas").clientWidth || 0)<760;
  function renderTree(root) {
    const host=byId("mind-tree"),compact=compactTree();
    const plan=MindTree.layout(root,compact?{levelWidths:[150,190,210,230],levelGap:28,rowHeight:28,rowGap:6,groupGap:8}:{});
    // The line layer shares the tree's box, so SVG units equal the layout's pixels.
    host.replaceChildren(mindLinks);
    for(const entry of plan.nodes){entry.element=treeElement(entry);host.append(entry.element);}
    host.style.width=plan.width+"px";host.style.height=plan.height+"px";
    plan.compact=compact;mindPlan=plan;
  }
  const viewSize=()=>({width:treeHostEl.clientWidth || 800,height:treeHostEl.clientHeight || 520});
  const treeSize=()=>({width:mindPlan?.width || 1,height:mindPlan?.height || 1});
  function applyCamera(animate=true) {
    treeEl.classList.toggle("mind-instant",!animate);
    treeEl.style.transform=`translate(${Math.round(mindCamera.x)}px,${Math.round(mindCamera.y)}px) scale(${mindCamera.k})`;
    byId("mind-zoom-level").textContent=`${Math.round(mindCamera.k*100)}%`;
  }
  function fitTree(options={}) {
    if(!mindPlan)return;const rootEntry=mindPlan.nodes[0];
    mindCamera=MindTree.fit(viewSize(),treeSize(),{min:.55,focusY:rootEntry.y+rootEntry.height/2,...options});applyCamera();
  }
  function revealEntries(entries,animate=true) {
    if(!mindPlan || !entries.length)return;
    mindCamera=MindTree.clamp(MindTree.reveal(mindCamera,viewSize(),MindTree.bounds(entries)),viewSize(),treeSize());applyCamera(animate);
  }
  // Shows a branch together with the children it just opened.
  function revealKey(key) {
    const parent=mindPlan?.nodes.find(entry=>entry.node.key===key);if(!parent)return;
    const children=mindPlan.nodes.filter(entry=>entry.parent===parent),view=viewSize();
    // On a narrow box the parent and its children cannot share the width; the opened children win.
    const whole=MindTree.bounds([parent,...children]);
    revealEntries(children.length && whole.width*mindCamera.k>view.width-48?children:[parent,...children]);
  }
  function zoomTree(factor,point=null) {
    if(!mindPlan)return;const view=viewSize();
    mindCamera=MindTree.clamp(MindTree.zoomAt(mindCamera,point || {x:view.width/2,y:view.height/2},factor),view,treeSize());applyCamera(point===null);
  }
  function syncMindMotion() {
    document.body.classList.toggle("mind-motion-off",mindMotionQuery.matches || mindMotionControl.checked);
    for(const element of byId("mind-canvas").querySelectorAll(".mind-node-reveal,.mind-edge-reveal")) {
      element.classList.remove("mind-node-reveal","mind-edge-reveal");element.style.removeProperty("--mind-delay");
    }
    mindReveal=null;
    scheduleMindDraw();
  }
  function scheduleMindDraw(reveal=null) {
    cancelAnimationFrame(mindDrawFrame);
    // A requested reveal is remembered rather than tied to one frame: detail toggles and data
    // arrival reschedule the draw before that frame runs, and would otherwise drop it.
    if(typeof reveal==="string" && reveal)mindReveal={parent:reveal,at:Date.now(),startedAt:null};
    mindDrawFrame=requestAnimationFrame(()=>{mindDrawFrame=0;drawMindLinks();});
  }
  function drawMindLinks() {
    const reduced=mindMotionQuery.matches || mindMotionControl.checked;
    // A reveal waits up to 3s for its branch to get nodes (session data may still be loading), then lives 1s.
    if(mindReveal && (reduced || Date.now()-(mindReveal.startedAt ?? mindReveal.at)>(mindReveal.startedAt?1000:3000)))mindReveal=null;
    // Redraws during a live reveal re-apply it with a negative delay so the animation continues in place.
    const reveal=mindReveal,elapsed=reveal?.startedAt?Date.now()-reveal.startedAt:0;
    const svg=mindLinks;svg.replaceChildren();
    if(mindSessionListOpen || !mindSession || !mindPlan)return;
    const width=Math.max(1,mindPlan.width),height=Math.max(1,mindPlan.height);
    svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.setAttribute("width",String(width));svg.setAttribute("height",String(height));
    const measured=element=>Math.max(0,element.offsetWidth || element.getBoundingClientRect().width || 0);
    const revealed=[];
    for(const {from,to} of mindPlan.links) {
      if(!from.element || !to.element)continue;
      // Branches leave the end of a label's line and arrive at the start of the child's line; the root leaves its box mid-height.
      const x1=from.x+Math.min(measured(from.element),from.width),y1=from.depth===0?from.y+from.height/2:from.y+from.height;
      const x2=to.x,y2=to.y+to.height,mid=(x1+x2)/2;
      const evidence=to.node.kind==="answer-evidence",focused=Boolean(to.node.selected || to.node.highlight);
      const path=svgEl("path",{d:`M ${x1} ${y1} C ${mid} ${y1},${mid} ${y2},${x2} ${y2}`,class:"mind-edge"+(focused?" mind-edge-active":"")+(evidence?" mind-evidence-edge":""),pathLength:1});
      path.dataset.parent=from.node.key;path.dataset.key=to.node.key;
      if(reveal && from.node.key===reveal.parent) {
        const order=revealed.push(to)-1;
        if(!evidence){path.classList.add("mind-edge-reveal");path.style.setProperty("--mind-delay",`${order*38-elapsed}ms`);}
      }
      svg.append(path);
    }
    if(reveal) {
      for(const [index,entry] of revealed.entries()) {
        const node=entry.element;
        if(!node.classList.contains("mind-node") || node.classList.contains("mind-node-reveal"))continue;
        node.style.setProperty("--mind-delay",`${130+index*38-elapsed}ms`);node.classList.add("mind-node-reveal");
      }
      if(reveal.startedAt===null && revealed.length)reveal.startedAt=Date.now();
    }
  }
  // A session's branches: numbered topics, then the folded auxiliary group. Under a project root the
  // questions are leaves (four levels is the cap); re-rooting at the session opens their answers.
  function topicBranches(group,topics,branch,leafQuestions=false) {
    const auxiliaryMode=mindTopicGroup==="auxiliary",failed=qaFailedSessions.has(mindSession),children=[];
    const primary=topics.filter(topic=>topic.displayGroup!=="auxiliary"),auxiliary=topics.filter(topic=>topic.displayGroup==="auxiliary");
    if(!topics.length)children.push(note(failed?"이 세션의 분석을 불러오지 못했습니다. 새로고침해 다시 확인해 주세요.":qaLoading?"저장된 분석을 확인하는 중…":"저장된 질문이 없습니다. 원문 대화를 확인할 수 있습니다."));
    primary.forEach((topic,index)=>children.push(topicNode(group,topic,String(index+1),false,branch,leafQuestions)));
    if(auxiliary.length)children.push({kind:"auxiliary-group",key:"auxiliary",label:"보조 정보",title:`보조 정보 ${auxiliary.length}개 · 기본 주제에서 옮겨 둔 묶음`,
      expanded:auxiliaryMode,selected:auxiliaryMode,count:auxiliary.length,
      children:auxiliary.map((topic,index)=>topicNode(group,topic,"보조 "+(index+1),true,branch,leafQuestions)),
      action:()=>{mindTopicGroup=auxiliaryMode?"primary":"auxiliary";mindTopic=null;mindQuestion="";mindAnswer="";close();rememberMind();renderMind(auxiliaryMode?null:"auxiliary");}});
    return children;
  }
  function analysedTree(session,group,topics,branch) {
    const title=sessionTitle(session);
    return {kind:"session",key:session.id,label:title,title:`${title}\n누르면 프로젝트 트리로 올라갑니다`,expanded:true,selected:true,
      children:topicBranches(group,topics,branch),action:leaveSessionRoot};
  }
  // Project root: sessions are numbered branches in recent order; the open one shows its topics or request groups.
  function projectTree(project,rows,open) {
    const root={kind:"project",key:project,label:project,title:`${project}\n프로젝트 · 세션 ${rows.length}개 · 누르면 세션 목록을 엽니다`,expanded:true,selected:true,children:[],action:()=>showSessionView(true)};
    if(!rows.length)root.children.push(note(catalogReady?"이 프로젝트에 세션이 없습니다.":"세션을 불러오고 있습니다…"));
    rows.forEach((row,index)=>root.children.push(sessionBranch(row,String(index+1),row.id===mindSession?open:null)));
    return root;
  }
  function sessionBranch(row,number,open) {
    const title=sessionTitle(row),stamp=activityValue(row),analysed=row.hasAnalysis!==false;
    const activity=Number.isFinite(stamp)?qaTime.format(new Date(stamp)):"활동 시각 미확정";
    const topicTitles=analysed?(qaGroups.find(entry=>entry.sessionId===row.id)?.topics.map(topic=>topic.title) || []):[];
    const preview=row.requestPreviews?.first?.text;
    const spec={kind:"session",key:row.id,number,label:title,expanded:Boolean(open),selected:Boolean(open),children:[],
      title:`${title}\n${row.provider==='claude'?'Claude Code':'Codex'} · ${activity} · ${analysed?(topicTitles.length?"주제 "+topicTitles.length+"개":"분석 있음"):"분석 대기"}${row.messageCount!==undefined?" · 메시지 "+row.messageCount+"개":""}${preview?"\n"+preview:""}\n두 번 누르면 이 세션만 봅니다`,
      count:topicTitles.length,badge:analysed?null:"대기",data:{analysis:analysed?"ready":"pending"},
      action:()=>selectSessionBranch(row),enter:()=>rootAtSession(row.id)};
    if(!open)spec.hits=countHits(analysed?topicTitles:[preview,row.requestPreviews?.latest?.text]);
    else spec.children=open.raw?rawBranches(open.session,open.raw):topicBranches(open.group,open.topics,open.branch,true);
    return spec;
  }
  function selectSessionBranch(row) {
    const open=row.id===mindSession;
    mindSession=open?"":row.id;mindTopic=null;mindQuestion="";mindAnswer="";mindTopicGroup="primary";
    close();rememberMind();renderMind(open?null:row.id);
  }
  function rootAtSession(id) {
    mindSession=id;mindRootKind="session";mindSessionListOpen=false;close();rememberMind();render();
  }
  // Up from a session root is its project's tree; a session without a project goes back to the list.
  function leaveSessionRoot() {
    const session=catalogRows().find(row=>row.id===mindSession);
    if(session?.project && [...byId("project").options].some(option=>option.value===session.project)) {
      byId("project").value=session.project;restoredMindFilters=null;mindRootKind="project";mindPage=0;close();rememberMind();render();
      byId("mind-canvas").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});
    } else showSessionView(true);
  }
  function topicNode(group,topic,number,auxiliary,branch,leafQuestions=false) {
    const open=topic.value===mindTopic && (mindTopicGroup==="auxiliary")===auxiliary;
    const spec={kind:"topic",key:topic.value,number,label:topic.title,title:`${topic.title}\n${auxiliary?"보조 정보":topicOrderLabel(topic)}`,
      expanded:open,selected:open,children:[],data:{group:auxiliary?"auxiliary":"primary"},action:()=>selectTopic(topic,auxiliary)};
    if(auxiliary) {
      const items=group?QuestionAnswers.itemsForTopic(data,group,topic.value):[];
      spec.count=items.length;
      if(!open)spec.hits=countHits(items.map(item=>nodeTitle(nodes.get(item.nodeId),item.source)));
      if(open)spec.children=items.length?items.map(item=>{
        const node=nodes.get(item.nodeId),key=JSON.stringify([item.source.session_id,item.source.source_node,node.id]),name=nodeTitle(node,item.source);
        return {kind:"auxiliary-item",key,label:name,title:`${name}\n${label(node)} · 누르면 근거와 관계를 봅니다`,selected:selected===node.id,
          data:{nodeId:node.id,sourceNode:item.source.source_node},action:()=>choose(node.id,item.source)};
      }):[note("저장된 보조 항목이 없습니다.","aux-empty")];
    } else {
      spec.count=topic.count;
      if(!open)spec.hits=countHits(QuestionAnswers.mindBranch(group,topic.value,"",auxiliary?"auxiliary":"primary").questions.map(entry=>nodeTitle(nodes.get(entry.nodeId),entry.source)));
      if(open)spec.children=branch.questions.length?branch.questions.map((entry,index)=>questionNode(entry,leafQuestions?"":`${number}.${index+1}`,branch,leafQuestions)):[note("이 주제에 저장된 질문이 없습니다.","q-empty")];
    }
    return spec;
  }
  function questionNode(entry,number,branch,leaf=false) {
    const key=QuestionAnswers.occurrenceKey(entry),node=nodes.get(entry.nodeId),open=key===mindQuestion,name=nodeTitle(node,entry.source);
    const spec={kind:"question",key,number,label:name,expanded:open && !leaf,selected:open,children:[],count:leaf?0:entry.answers.length+entry.answerEvidence.length,
      title:`${name}\n${entry.anchor?"원문 #"+(entry.anchor.index+1):"순서 미확정"} · 답변 연결 ${entry.answers.length}개${entry.answerEvidence.length?" · 답변 근거 "+entry.answerEvidence.length+"개":""}`,
      data:{nodeId:entry.nodeId,sourceNode:entry.source.source_node},
      action:()=>{if(open){mindQuestion="";mindAnswer="";close();rememberMind();renderMind();}else selectMindQuestion(entry);}};
    if(!open && !leaf)spec.hits=countHits([...entry.answers.map(answer=>{const node=nodes.get(answer.nodeId);return nodeTitle(node,answer.source || resolveSource(node));}),...entry.answerEvidence.map(evidence=>evidenceTitle(entry,evidence))]);
    if(open && !leaf) {
      spec.children=[...branch.answers.map(answer=>answerNode(answer)),...branch.answerEvidence.map(evidence=>evidenceNode(entry,evidence))];
      if(!spec.children.length)spec.children=[note("별도 답변 관계와 확인된 답변 근거가 없습니다.","a-empty")];
    }
    return spec;
  }
  function answerNode(answer) {
    const node=nodes.get(answer.nodeId),source=answer.source || resolveSource(node),key=answer.nodeId,name=nodeTitle(node,source),active=mindAnswer===key;
    if(active)mindAnswerSelection={kind:"answer",node,source};
    return {kind:"answer",key,label:name,title:`${name}\n답변 · 누르면 원문을 함께 읽습니다`,selected:active,highlight:!mindAnswer,
      data:{nodeId:answer.nodeId,edgeIds:answer.edgeIds.join(" ")},
      action:()=>{mindAnswer=key;
        if(answer.anchor && liveCatalog){selected=answer.nodeId;selectedSource=source;close();rememberMind();renderMind();explorer.openReader(source.session_id,answer.anchor.messageId);}
        else {rememberMind();choose(answer.nodeId,source);}}};
  }
  function evidenceNode(entry,evidence) {
    const key=JSON.stringify([entry.source.session_id,entry.source.source_node,evidence.messageId]),name=evidenceTitle(entry,evidence),active=mindAnswer===key;
    const original=`어시스턴트 원문 #${evidence.index+1}`;
    if(active)mindAnswerSelection={kind:"evidence",entry,evidence};
    return {kind:"answer-evidence",key,label:name,title:`${name}\n${name!==original?original+" · ":""}저장된 답변 근거 · 검증된 인용 ${evidence.evidenceCount}개${evidence.quotes.length?"\n"+evidence.quotes[0]:""}`,
      selected:active,highlight:!mindAnswer,data:{messageId:evidence.messageId},
      action:()=>{mindAnswer=key;rememberMind();if(liveCatalog){renderMind();explorer.openReader(entry.source.session_id,evidence.messageId);}else choose(entry.nodeId);}};
  }
  function rawTree(session,raw) {
    const title=sessionTitle(session);
    return {kind:"session",key:session.id,label:title,title:`${title}\n누르면 프로젝트 트리로 올라갑니다`,expanded:true,selected:true,children:rawBranches(session,raw),action:leaveSessionRoot};
  }
  function rawBranches(session,raw) {
    const children=[],turns=raw.turns,chosen=raw.chosen;
    if(raw.offline)children.push(note("원문은 실행 중인 뷰어에서 읽을 수 있습니다."));
    else if(raw.failed)children.push(note("원문 묶음을 불러오지 못했습니다. 원문 화면에서 확인하세요."));
    else if(raw.loading)children.push(note("원문 순서를 확인하는 중…"));
    else if(!turns.length)children.push(note("저장된 사용자 요청이 없습니다."));
    const requestNode=(turn,number)=>{
      const open=turn===chosen,preview=turn.request.preview || "사용자 요청";
      return {kind:"raw-request",key:turn.request.messageId,number,label:preview,title:`${preview}\n원문 #${turn.request.index+1}`,expanded:open,selected:open,count:turn.responses.length,
        hits:open?0:countHits(turn.responses.map(item=>item.preview)),data:{messageId:turn.request.messageId},
        action:()=>{if(open)raw.clear();else{mindReveal={parent:turn.request.messageId,at:Date.now(),startedAt:null};raw.select(turn);revealKey(turn.request.messageId);}},
        children:!open?[]:turn.responses.length?turn.responses.map(item=>{
          const text=item.preview || "어시스턴트 메시지";
          return {kind:"raw-response",key:item.messageId,label:text,title:`${text}\n원문 #${item.index+1} · 시간순 참조`,selected:mindAnswer===item.messageId,highlight:!mindAnswer,
            data:{messageId:item.messageId},action:()=>{mindAnswer=item.messageId;rememberMind();renderMind();explorer.openReader(session.id,item.messageId);}};
        }):[note("뒤이어 저장된 어시스턴트 메시지가 없습니다.","r-empty")]};
    };
    const size=10;
    if(turns.length<=size)turns.forEach((turn,index)=>children.push(requestNode(turn,String(index+1))));
    else for(let start=0;start<turns.length;start+=size) {
      const index=start/size,key="raw:"+index,open=mindTopic===key,slice=turns.slice(start,start+size);
      children.push({kind:"raw-group",key,number:String(index+1),label:`요청 ${start+1}–${start+slice.length}`,title:`사용자 요청 ${start+1}번부터 ${start+slice.length}번까지`,
        expanded:open,selected:open,count:slice.length,hits:open?0:countHits(slice.map(turn=>turn.request.preview)),children:open?slice.map((turn,offset)=>requestNode(turn,`${index+1}.${offset+1}`)):[],
        action:()=>{mindTopic=open?null:key;rememberMind();renderMind(open?null:key);}});
    }
    return children;
  }
  function selectTopic(topic,auxiliary) {
    const open=topic.value===mindTopic && (mindTopicGroup==="auxiliary")===auxiliary;
    mindQuestion="";mindAnswer="";
    if(open)mindTopic=null;else {mindTopic=topic.value;mindTopicGroup=auxiliary?"auxiliary":"primary";}
    close();rememberMind();renderMind(open?null:topic.value);
  }
  function renderMind(reveal=null,catalogOnly=false) {
    cancelAnimationFrame(mindDrawFrame);
    const active=document.activeElement;
    const focusHost=["mind-tree","mind-sessions"].find(id=>active?.closest?.("#"+id));
    const focus=focusHost ? {kind:active.dataset.kind,key:active.dataset.key,label:active.getAttribute("aria-label"),host:focusHost} : null;
    const term=byId("query").value.trim().toLocaleLowerCase(),project=byId("project").value;
    // The search box narrows the session list; an open session stays put and has its matching branches lit instead.
    const rows=scopedCatalog().slice().sort(recentSessions).filter(session=>!project || session.project===project);
    const catalog=rows.filter(session=>!term || sessionSearchText(session).includes(term));
    if(catalogReady && mindSession && !rows.some(session=>session.id===mindSession)) {mindSession="";mindTopic=null;mindQuestion="";mindAnswer="";catalogOnly=false;}
    const session=rows.find(session=>session.id===mindSession),group=qaGroups.find(group=>group.sessionId===mindSession);
    mindTerm=term;
    // A chosen project is the root and its sessions are branches; otherwise the tree needs a session, so the list stays up.
    const projectRoot=mindRootKind==="project" && project ? project : "";
    explorer.updateSession(session?.id || "");
    byId('explorer-analyze').disabled=!session || !liveCatalog || window.parent===window;
    const listOpen=mindSessionListOpen || (!session && !projectRoot);
    byId("session-mindmap").setAttribute("aria-label",listOpen?"세션 목록":"세션 마인드맵");
    byId("mind-canvas").classList.toggle("session-picker-open",listOpen);
    byId("mind-session-picker").hidden=!listOpen;
    byId("mind-tree-host").hidden=listOpen;byId("mind-zoom").hidden=listOpen;
    byId("mind-session-list-info").textContent=`${catalog.length}개 · 최근 대화순 · 원문 미리보기`;
    byId("mind-session-change").hidden=!session && !projectRoot;
    byId("mind-session-change").textContent=listOpen?"마인드맵으로 돌아가기":"세션 목록 펼치기";
    renderSessionEditor("mind",session);
    const allTopics=group?QuestionAnswers.orderedTopics(group):[];
    const auxiliaryMode=mindTopicGroup==="auxiliary",ready=!qaLoading && !qaFailedSessions.has(mindSession);
    if(ready && mindTopic!==null && !isRawTopic(mindTopic) && !allTopics.some(topic=>topic.value===mindTopic && (topic.displayGroup==="auxiliary")===auxiliaryMode)) {mindTopic=null;mindQuestion="";mindAnswer="";}
    const branch=QuestionAnswers.mindBranch(group,mindTopic,mindQuestion,mindTopicGroup);
    if(ready && mindQuestion && !branch.question) {mindQuestion="";mindAnswer="";}
    renderTopicEditor("mind",group,mindTopic);
    if(listOpen)sessionPage(catalog,!catalogReady ? "세션을 불러오고 있습니다…" : "조건에 맞는 세션이 없습니다.");
    else byId("mind-sessions").replaceChildren();
    const raw=rawTurns.view(session && !listOpen ? session : null);
    byId("mind-canvas").classList.toggle("raw-chronology",Boolean(raw));
    // New raw sessions do not change the saved analysis: a catalogue-only refresh keeps the
    // branch DOM and its motion and focus intact, updating only the root's label.
    const treeKey=projectRoot?"project:"+projectRoot:session?"session:"+session.id:"";
    // A project tree is rebuilt on every catalogue change so new sessions appear as branches.
    const treeHost=byId("mind-tree"),sameTree=Boolean(treeKey) && !projectRoot && !listOpen && catalogOnly && treeHost.dataset.tree===treeKey;
    treeHost.classList.toggle("searching",Boolean(term) && Boolean(treeKey) && !listOpen);
    if(!treeKey || listOpen) {treeHost.replaceChildren(mindLinks);treeHost.dataset.tree="";mindPlan=null;mindAnswerSelection=null;}
    else if(sameTree) {const name=[...treeHost.querySelectorAll(".mind-node")].find(node=>node.dataset.kind==="session")?.querySelector(".name");if(name)name.textContent=sessionTitle(session);}
    else {
      mindAnswerSelection=null;mindSearchHits=0;
      renderTree(projectRoot?projectTree(projectRoot,rows,{session,group,topics:allTopics,branch,raw}):raw?rawTree(session,raw):analysedTree(session,group,allTopics,branch));
      treeHost.dataset.tree=treeKey;
      // A new root is fitted; afterwards the camera only moves enough to show what opened.
      const fresh=mindCameraRoot!==treeKey;
      if(fresh){mindCameraRoot=treeKey;fitTree();}
      const chosen=mindPlan.nodes.filter(entry=>entry.depth>0 && entry.node.selected).at(-1);
      if(reveal && !fresh)revealKey(reveal);
      else if(chosen && (fresh || reveal))revealEntries([chosen]);
      else {mindCamera=MindTree.clamp(mindCamera,viewSize(),treeSize());applyCamera();}
    }
    const questionIndex=branch.questions.findIndex(entry=>QuestionAnswers.occurrenceKey(entry)===mindQuestion);
    byId("question-stepper").hidden=listOpen || (auxiliaryMode && !raw);
    if(raw) {
      byId("question-previous").disabled=raw.index<=0;
      byId("question-next").disabled=!raw.turns.length || raw.index>=raw.turns.length-1;
      byId("question-position").textContent=raw.index<0?`요청 ${raw.turns.length}개`:`${raw.index+1} / ${raw.turns.length}`;
    } else {
      byId("question-previous").disabled=questionIndex<=0;
      byId("question-next").disabled=auxiliaryMode || !branch.questions.length || questionIndex>=branch.questions.length-1;
      byId("question-position").textContent=questionIndex<0?`질문 ${branch.questions.length}개`:`${questionIndex+1} / ${branch.questions.length}${branch.question?.anchor?"":" · 순서 미확정"}`;
    }
    byId("mind-question-title-action").hidden=listOpen || auxiliaryMode || !branch.question;
    byId("mind-answer-title-action").hidden=listOpen || !mindAnswerSelection;
    const trail=byId("mind-selection");trail.replaceChildren();
    if(projectRoot) {
      const name=el("strong",projectRoot);name.title=projectRoot;
      trail.append(el("span","프로젝트","breadcrumb-label"),name);
      if(!session)trail.append(el("span","세션 가지를 누르면 주제가 펼쳐지고, 두 번 누르면 그 세션만 봅니다.","hint"));
    } else if(session?.project) {
      // The project sits above a session root; its crumb climbs back to the project tree.
      const up=button(session.project,leaveSessionRoot,"breadcrumb-up");up.title="프로젝트 트리로 올라갑니다";
      trail.append(el("span","프로젝트","breadcrumb-label"),up);
    }
    if(session) {
      const name=el("strong",sessionTitle(session));name.title=sessionTitle(session);
      if(projectRoot || session.project)trail.append(el("span","/","breadcrumb-arrow"));
      trail.append(el("span","세션","breadcrumb-label"),name);
      if(raw?.chosen) {
        const text=raw.chosen.request.preview || "사용자 요청",request=el("strong",text);request.title=text;
        trail.append(el("span","/","breadcrumb-arrow"),el("span","요청","breadcrumb-label"),request);
      } else if(!raw && mindTopic!==null) {
        const title=allTopics.find(topic=>topic.value===mindTopic)?.title || "주제 미확정",topic=el("strong",title);topic.title=title;
        trail.append(el("span","/","breadcrumb-arrow"),el("span",auxiliaryMode?"보조 정보":"주제","breadcrumb-label"),topic);
      }
      if(branch.question){const name=el("strong",nodeTitle(nodes.get(branch.question.nodeId),branch.question.source));name.title=name.textContent;trail.append(el("span","/","breadcrumb-arrow"),el("span","질문","breadcrumb-label"),name);}
      const notice=assignmentNotice(session);if(notice)trail.append(el("span",notice,"mind-selection-note"));
      if(term && !listOpen)trail.append(el("span",`검색 "${byId("query").value.trim()}" · 맞는 가지 ${mindSearchHits}개`,"mind-selection-note mind-search-note"));
    } else if(!projectRoot) trail.append(el("span","먼저 세션을 선택하세요. 최근 대화순으로 표시합니다.","hint"));
    if(!catalogOnly) {
    const actions=byId("mind-actions");actions.replaceChildren();
    if(projectRoot && session && !listOpen)actions.append(button("이 세션만 보기",()=>rootAtSession(session.id),"root-session"));
    if(listOpen) {}
    else if(projectRoot && !session) {}
    else if(raw) {
      const whole=button("이 세션의 원문 읽기",()=>openSession(session.id));whole.disabled=!liveCatalog;actions.append(whole);
      if(raw.chosen){const source=button("이 요청의 앞뒤 원문 읽기",()=>explorer.openReader(session.id,raw.chosen.request.messageId),"source-open");source.disabled=!liveCatalog;actions.append(source);}
    } else if(auxiliaryMode && mindTopic!==null) {
      actions.append(el("p","보조 항목을 누르면 기존 내용과 원문 근거, 저장된 관계를 확인할 수 있습니다.","hint"));
    } else {
      if(mindAnswerSelection?.kind==="evidence") {
        const {entry,evidence}=mindAnswerSelection;
        actions.append(el("p",`저장된 답변 근거 · ${evidenceTitle(entry,evidence)} · 검증된 인용 ${evidence.evidenceCount}개`,"qa-label"));
        if(evidence.quotes.length)actions.append(el("blockquote",evidence.quotes.join("\n\n"),"answer-evidence-preview"));
        else actions.append(el("p","인용 내용은 질문·근거 보기에서 확인할 수 있습니다.","hint"));
      } else if(mindAnswerSelection?.kind==="answer") {
        const {node,source}=mindAnswerSelection;
        actions.append(el("p","연결된 분석 답변 · "+nodeTitle(node,source),"qa-label"),el("p",node.summary || "요약 없음"));
      }
      if(branch.question) {
        const entry=branch.question,node=nodes.get(entry.nodeId);actions.append(el("h2",nodeTitle(node,entry.source)),el("p",node.summary || "요약 없음"));
        const source=button("이 질문의 앞뒤 원문 읽기",()=>{rememberMind();openSource(entry.source,source,entry.anchor.sourceMessageId);},"source-open");
        source.disabled=!entry.anchor || !liveCatalog;
        actions.append(source,button("질문·근거 보기",()=>choose(entry.nodeId,entry.source)));
      } else {
        const evidence=allTopics.find(topic=>topic.value===mindTopic)?.userEvidence;
        if(evidence) {
          actions.append(el("p",`확인된 질문 위치가 없어, 같은 주제의 검증된 사용자 근거 원문 #${evidence.index+1}로 주제 순서를 보완했습니다.`,"hint"));
          const original=button("이 주제의 사용자 근거 원문 읽기",()=>openSource({session_id:mindSession,source_node:evidence.sourceNodeId},original,evidence.sourceMessageId),"topic-source-open");
          original.disabled=!liveCatalog;actions.append(original);
        }
        const source=button("이 세션의 원문 읽기",()=>openSession(mindSession));source.disabled=!liveCatalog;actions.append(source);
      }
    }
    }
    byId("info").textContent=`${{primary:"일반 세션",subagent:"보조 작업",unknown:"분류 미확정"}[catalogKind]} ${catalog.length}개`;
    const notice=byId("mind-session-notice");notice.replaceChildren();
    notice.hidden=listOpen || !session || session.hasAnalysis!==false;
    if(!notice.hidden) {
      notice.append(el("span","원문 순서 묶음 · 분석 전 세션은 요청과 응답을 원문 순서로 보여줍니다. 주제 가지는 분석 뒤에 생깁니다."));
      const analyze=button("이 세션 분석",()=>byId('explorer-analyze').click());analyze.disabled=byId('explorer-analyze').disabled;
      notice.append(analyze,button("원문 읽기",()=>explorer.readSession(session.id)));
    }
    byId("view-info").textContent=qaLoading?"저장된 분석과 원문 근거를 확인하고 있습니다…":(qaFailed?"일부 세션의 분석·원문 순서를 불러오지 못했습니다. 새로고침해 다시 확인해 주세요. ":"")+"주제를 누르면 질문이, 질문을 누르면 답변이 펼쳐집니다. 실선은 소속과 저장된 답변 관계, 점선은 질문에 저장된 답변 근거이며, 원문 순서가 없는 질문은 순서 미확정으로 표시합니다.";
    if(focus) {
      const host=byId(focus.host);
      const target=focus.kind ? [...host.querySelectorAll(".mind-node")].find(node=>node.dataset.kind===focus.kind&&node.dataset.key===focus.key)
        : [...host.querySelectorAll("button")].find(node=>node.getAttribute("aria-label")===focus.label&&!node.disabled) || host.querySelector(".mind-node");
      target?.focus({preventScroll:true});
    }
    scheduleMindDraw(reveal);
  }
  function reducedMotion() {return mindMotionQuery.matches || mindMotionControl.checked;}
  function centerMindSelection() {
    const open=document.body.classList.contains('has-source-reader'),revealed=open&&!sourcePanelWasOpen;sourcePanelWasOpen=open;
    if(mindSessionListOpen)return;
    requestAnimationFrame(()=>{
      if(revealed)byId('mind-canvas').scrollIntoView({block:'start',behavior:'auto'});
      // The reader shortens the tree box; the chosen branch is panned back into view rather than the page scrolled.
      const chosen=mindPlan?.nodes.filter(entry=>entry.depth>0 && entry.node.selected).at(-1);
      if(chosen)revealEntries([chosen]);
      scheduleMindDraw();
    });
  }
  function selectMindQuestion(entry) {
    const key=QuestionAnswers.occurrenceKey(entry);
    mindQuestion=key;mindAnswer="";selected=null;close();rememberMind();renderMind(key);
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
    const changed=mindSession!==session.id;mindSession=session.id;mindSessionListOpen=false;
    catalogKind=sessionKind(session);catalogParent=catalogKind==="subagent"?parentKey(session):"";
    byId("query").value="";byId("project").value="";restoredMindFilters=null;mindRootKind="session";
    if(changed){mindTopic=null;mindQuestion="";mindAnswer="";mindTopicGroup="primary";}
    if(target.sourceMessage && session.hasAnalysis===false)rawTurns.target(target.sourceMessage);
    const group=qaGroups.find(g=>g.sessionId===session.id),entry=group?.entries.find(e=>QuestionAnswers.occurrenceKey(e)===target.questionKey);
    if(entry){const topic=group.topics.find(t=>t.id===entry.topicId);mindTopicGroup=topic?.displayGroup==="auxiliary"?"auxiliary":"primary";
      mindTopic=entry.topicId?"topic:"+entry.topicId:"unassigned";mindQuestion=QuestionAnswers.occurrenceKey(entry);mindAnswer="";
      selected=entry.nodeId;selectedSource=entry.source;
    }
    mindPage=Math.max(0,Math.floor(scopedCatalog().slice().sort(recentSessions).findIndex(s=>s.id===session.id)/8));
    close();rememberMind();render();centerMindSelection();byId("mind-canvas").scrollIntoView({block:"start",behavior:"auto"});
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
  function choose(id,source=null) {
    if(!nodes.has(id)) return;
    selectedSource=resolveSource(nodes.get(id),source);
    selected=id;
    document.body.classList.add("has-detail");byId("detail-panel").hidden=false;
    history.replaceState(null,"","#"+new URLSearchParams({node:id}));
    details();byId("detail-panel").scrollTop=0;render();
  }
  function render() {
    byId('explorer-analyze').disabled=!liveCatalog || window.parent===window || !mindSession;
    if(byId("item-title-dialog").open)renderItemTitleEditor();
    renderMind();
    explorer.refresh();
  }
  if(restoredMindFilters) byId("query").value=restoredMindFilters.query;
  else restoredMindFilters=null;
  updateProjectOptions();
  byId("totals").textContent=`${data.nodes.length}개 항목 · ${data.edges.length}개 관계 · 분석된 세션 ${data.sessionCount}개`;
  // The project filter doubles as the root choice: a project roots the tree, "모든 프로젝트" returns to the session root.
  for(const [id,event] of [["query","input"],["project","change"]])byId(id).addEventListener(event,()=>{
    if(id==="project"){mindRootKind=byId("project").value?"project":"session";if(mindRootKind==="project")mindSessionListOpen=false;}
    restoredMindFilters=null;mindPage=0;render();rememberMind();});
  byId("reset").addEventListener("click",()=>{for(const id of ["query","project"])byId(id).value="";mindRootKind="session";restoredMindFilters=null;mindPage=0;render();rememberMind();});
  for(const id of ["catalog-kind","catalog-parent"]) byId(id).addEventListener("change",()=>{
    if(id==="catalog-kind") catalogKind=byId(id).value;
    else catalogParent=byId(id).value;
    mindSession="";mindTopic=null;mindQuestion="";mindAnswer="";mindTopicGroup="primary";mindPage=0;
    render();rememberScope();rememberMind();
  });
  byId("mind-session-change").addEventListener("click",()=>{
    showSessionView(!mindSessionListOpen);
  });
  byId("mind-question-title-edit").addEventListener("click",()=>{
    const group=qaGroups.find(group=>group.sessionId===mindSession),entry=group?.entries.find(entry=>QuestionAnswers.occurrenceKey(entry)===mindQuestion);
    if(entry)openItemTitleEditor(itemTitleTarget(nodes.get(entry.nodeId),entry.source,"질문"));
  });
  byId("mind-answer-title-edit").addEventListener("click",()=>{
    const pick=mindAnswerSelection;if(!pick)return;
    if(pick.kind==="evidence")openItemTitleEditor({source:pick.entry.source,nodeId:pick.entry.nodeId,label:"답변 근거",evidenceMessage:pick.evidence.messageId,originalTitle:`어시스턴트 원문 #${pick.evidence.index+1}`});
    else openItemTitleEditor(itemTitleTarget(pick.node,pick.source,"답변"));
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
  for(const prefix of ["mind"]) {
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
  function showSessionView(list) {
    if(list===mindSessionListOpen || (!list && !mindSession && !(mindRootKind==="project" && byId("project").value)))return;
    mindSessionListOpen=list;
    history.replaceState(null,"",location.pathname+location.search);close();rememberMind();render();
    byId("mind-canvas").scrollIntoView({block:"start",behavior:reducedMotion()?"auto":"smooth"});
  }
  // Narrow screens get tighter levels; crossing that width re-lays the tree, anything else keeps the camera in bounds.
  window.addEventListener("resize",()=>{
    if(mindPlan && mindPlan.compact!==compactTree())renderMind();
    else {if(mindPlan){mindCamera=MindTree.clamp(mindCamera,viewSize(),treeSize());applyCamera(false);}scheduleMindDraw();}
  });
  // Pointer gestures on the tree box: one pointer drags, two pinch; a drag never counts as a click on a branch.
  const treePointers=new Map();let treeDrag=null,treePinch=null,treeMoved=false;
  const hostPoint=event=>{const rect=treeHostEl.getBoundingClientRect();return {x:event.clientX-rect.left,y:event.clientY-rect.top};};
  treeHostEl.addEventListener("pointerdown",event=>{
    if(!mindPlan || (event.button!==undefined && event.button!==0))return;
    treePointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(treePointers.size===1){treeDrag={x:event.clientX,y:event.clientY,camera:{...mindCamera}};treeMoved=false;}
    else if(treePointers.size===2){
      const [a,b]=[...treePointers.values()];treePinch={distance:Math.hypot(a.x-b.x,a.y-b.y),camera:{...mindCamera}};treeDrag=null;
      for(const id of treePointers.keys())treeHostEl.setPointerCapture?.(id);
    }
  });
  treeHostEl.addEventListener("pointermove",event=>{
    if(!treePointers.has(event.pointerId))return;
    treePointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(treePinch && treePointers.size>=2) {
      const [a,b]=[...treePointers.values()],rect=treeHostEl.getBoundingClientRect(),distance=Math.hypot(a.x-b.x,a.y-b.y);
      mindCamera=MindTree.zoomAt(treePinch.camera,{x:(a.x+b.x)/2-rect.left,y:(a.y+b.y)/2-rect.top},distance/Math.max(1,treePinch.distance));
      treeMoved=true;treeHostEl.classList.add("mind-dragging");applyCamera(false);return;
    }
    if(!treeDrag)return;
    const dx=event.clientX-treeDrag.x,dy=event.clientY-treeDrag.y;
    if(!treeMoved && Math.hypot(dx,dy)<4)return;
    // Capturing only once a drag is under way keeps plain taps and clicks on the branch buttons.
    if(!treeMoved)treeHostEl.setPointerCapture?.(event.pointerId);
    treeMoved=true;treeHostEl.classList.add("mind-dragging");
    mindCamera={x:treeDrag.camera.x+dx,y:treeDrag.camera.y+dy,k:treeDrag.camera.k};applyCamera(false);
  });
  const endTreePointer=event=>{
    treePointers.delete(event.pointerId);
    if(treePointers.size<2)treePinch=null;
    if(!treePointers.size){treeDrag=null;treeHostEl.classList.remove("mind-dragging");
      if(treeMoved){mindCamera=MindTree.clamp(mindCamera,viewSize(),treeSize());applyCamera();setTimeout(()=>{treeMoved=false;},0);}}
  };
  treeHostEl.addEventListener("pointerup",endTreePointer);treeHostEl.addEventListener("pointercancel",endTreePointer);
  treeHostEl.addEventListener("click",event=>{if(treeMoved){event.stopPropagation();event.preventDefault();}},true);
  treeHostEl.addEventListener("wheel",event=>{
    if(!mindPlan)return;event.preventDefault();
    if(event.ctrlKey || event.metaKey)zoomTree(Math.exp(-event.deltaY*0.002),hostPoint(event));
    else {mindCamera=MindTree.clamp({...mindCamera,x:mindCamera.x-event.deltaX,y:mindCamera.y-event.deltaY},viewSize(),treeSize());applyCamera(false);}
  },{passive:false});
  // Focus and scroll-into-view may scroll the clipped box; that scroll becomes a camera move, so the box itself stays put.
  treeHostEl.addEventListener("scroll",()=>{
    const dx=treeHostEl.scrollLeft || 0,dy=treeHostEl.scrollTop || 0;if(!dx && !dy)return;
    treeHostEl.scrollLeft=0;treeHostEl.scrollTop=0;
    if(mindPlan){mindCamera={...mindCamera,x:mindCamera.x-dx,y:mindCamera.y-dy};applyCamera(false);}
  });
  byId("mind-zoom-in").addEventListener("click",()=>zoomTree(1.25));
  byId("mind-zoom-out").addEventListener("click",()=>zoomTree(0.8));
  byId("mind-zoom-reset").addEventListener("click",()=>zoomTree(1/mindCamera.k));
  byId("mind-fit").addEventListener("click",()=>fitTree({min:.2}));
  // Arrow keys walk the visible branches in reading order; → opens or enters a branch, ← folds it or steps back.
  treeEl.addEventListener("keydown",event=>{
    if(!mindPlan)return;const target=event.target?.closest?.(".mind-node");if(!target)return;
    const order=mindPlan.nodes.filter(entry=>entry.element?.classList.contains("mind-node"));
    const index=order.findIndex(entry=>entry.element===target);if(index<0)return;
    const current=order[index];let next=null;
    switch(event.key) {
      case "ArrowDown":next=order[index+1] || null;break;
      case "ArrowUp":next=order[index-1] || null;break;
      case "Home":next=order[0];break;
      case "End":next=order[order.length-1];break;
      case "ArrowRight": {
        const child=order.find(entry=>entry.parent===current);
        if(child)next=child;
        else if(!current.node.expanded && current.depth>0 && !["answer","answer-evidence","raw-response","auxiliary-item"].includes(current.node.kind)){event.preventDefault();target.click();return;}
        break;
      }
      case "ArrowLeft": {
        if(current.node.expanded && current.depth>0){event.preventDefault();target.click();return;}
        next=current.parent?.element?.classList.contains("mind-node")?current.parent:null;break;
      }
      default:return;
    }
    event.preventDefault();
    if(next){next.element.focus({preventScroll:true});revealEntries([next]);}
  });
  window.addEventListener("pagehide",()=>{
    rememberMind();
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
  function close(){byId("detail-panel").hidden=true;document.body.classList.remove("has-detail");}
  byId("close-detail").addEventListener("click",close);document.addEventListener("keydown",event=>{if(event.key==="Escape")close();});
  let navigationStorage=sessionStorage;try{navigationStorage=window.localStorage || sessionStorage;}catch(_){}
  byId('explorer-analyze').addEventListener('click',()=>{
    const sid=mindSession,session=catalogRows().find(s=>s.id===sid);
    if(!session || !liveCatalog || window.parent===window)return;
    window.parent.postMessage({type:'context-graph-analyze',session:sid,title:session.displayTitle||session.title},location.origin);
  });
  const explorer=SessionAtlasTools.create({document,fetch,storage:navigationStorage,live:liveCatalog,
    getSessions:catalogRows,getTargets:searchTargets,onNavigate:navigateExplorer,onLayout:centerMindSelection});
  const rawTurns=RawTurns.create({document,fetch,live:liveCatalog,
    openSource:(sid,message)=>explorer.openReader(sid,message),redraw:()=>{renderMind();},remember:rememberMind,
    locate:(index,total)=>{mindTopic=total>10?"raw:"+Math.floor(index/10):null;mindAnswer="";}});
  const requested=new URLSearchParams(location.hash.slice(1)).get("node");
  document.body.classList.add("mind-mode");byId("query").placeholder="세션·가지 검색";
  syncMindMotion();
  if(requested && nodes.has(requested)) choose(requested);else render();
  refreshCatalog();
  loadQuestionOrder();
})();
