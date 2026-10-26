/* Original-message chronology, never a stored topic or answers relationship. */
const RawTurns = (()=>{
  function create({document,fetch,live,page,card,openSource,redraw,remember,locate}) {
    const byId=id=>document.getElementById(id),cache=new Map();let session=null,selection="",pendingMessage="",generation=0,needsLocate=false;
    function saved(){try{const value=JSON.parse(sessionStorage.getItem('session-atlas-raw-turn')||'null');if(value?.session===session?.id)selection=value.message||"";}catch(_){}}
    function store(){try{sessionStorage.setItem('session-atlas-raw-turn',JSON.stringify({session:session.id,message:selection}));}catch(_){}remember?.();}
    function select(turn){selection=turn.request.messageId;locate(cache.get(session.id).turns.indexOf(turn));store();redraw();openSource(session.id,selection);}
    function render(current){
      if(!current || current.hasAnalysis!==false){session=null;generation++;return false;}
      if(session?.id!==current.id){session=current;selection="";saved();needsLocate=!!selection;}else session=current;
      const entry=cache.get(session.id);
      if(!live)return false;
      if(!entry || entry.catalogRevision!==current.revision){
        if(!entry?.loading){const id=session.id,request=++generation;cache.set(id,{...entry,loading:true,catalogRevision:current.revision});
          fetch('timeline/turns.json?'+new URLSearchParams({session:id}),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('Unavailable');return r.json();}).then(value=>{
            if(value.session?.id!==id || value.kind!=='chronological' || !Array.isArray(value.turns))throw new Error('Invalid chronology');
            cache.set(id,{...value,catalogRevision:current.revision});if(request===generation && session?.id===id)redraw();
          }).catch(()=>{cache.set(id,{catalogRevision:current.revision,failed:true});if(session?.id===id)redraw();});}
      }
      const data=cache.get(session.id),turns=data?.turns||[];
      if(pendingMessage && data?.turns){const found=turns.find(t=>t.request.messageId===pendingMessage || t.responses.some(r=>r.messageId===pendingMessage));if(found){selection=found.request.messageId;needsLocate=true;}pendingMessage="";}
      const chosen=turns.find(t=>t.request.messageId===selection),index=turns.indexOf(chosen);
      if(needsLocate && chosen){locate(index);needsLocate=false;store();}
      byId('mind-canvas').classList.add('raw-chronology');
      byId('mind-topics-title').textContent='원문 묶음';byId('mind-questions-title').textContent='사용자 요청 · 원문순';
      byId('mind-answers-title').textContent='이어진 어시스턴트 메시지';
      byId('mind-topic-groups').hidden=true;byId('mind-topic-editor').hidden=true;byId('mind-question-title-action').hidden=true;
      page(byId('mind-topics'),[{}],1,()=>card('저장된 원문 대화','의미 분석이 아닌 순서 묶음',true,()=>{},'raw-topic','raw:chronology'),'');
      page(byId('mind-questions'),turns,2,turn=>{const node=card(turn.request.preview||'사용자 요청',`원문 #${turn.request.index+1}`,turn===chosen,()=>select(turn),'raw-request',turn.request.messageId);node.dataset.messageId=turn.request.messageId;return node;},
        data?.failed?'원문 묶음을 불러오지 못했습니다. 원문 화면에서 확인하세요.':data?.loading?'원문 순서를 확인하는 중…':'저장된 사용자 요청이 없습니다.');
      page(byId('mind-answers'),chosen?.responses||[],3,item=>{const node=card(item.preview||'어시스턴트 메시지',`원문 #${item.index+1} · 시간순 참조`,false,()=>openSource(session.id,item.messageId),'raw-response',item.messageId);node.dataset.messageId=item.messageId;return node;},chosen?'뒤이어 저장된 어시스턴트 메시지가 없습니다.':'사용자 요청을 선택하세요.');
      byId('question-stepper').hidden=false;byId('question-previous').disabled=index<=0;byId('question-next').disabled=!turns.length||index>=turns.length-1;
      byId('question-position').textContent=index<0?`요청 ${turns.length}개`:`${index+1} / ${turns.length}`;
      byId('mind-step-2').disabled=!turns.length;byId('mind-step-3').disabled=!chosen;
      byId('mind-session-notice').hidden=false;
      byId('mind-session-notice').textContent='원문 순서 묶음 · 주제나 답변의 의미를 판단하지 않습니다. 기존 분석 노드·관계를 만들거나 변경하지 않습니다.';
      return true;
    }
    return {render,move(direction){const turns=cache.get(session?.id)?.turns||[],index=turns.findIndex(t=>t.request.messageId===selection),next=turns[index+direction];if(next)select(next);},
      target(message){pendingMessage=message||"";},active:()=>!!session};
  }
  return {create};
})();
